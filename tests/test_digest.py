from datetime import date, timedelta

from ragspine.business import expiry, kalendar
from ragspine.ops import digest
from ragspine.ops.scheduler import Scheduler


def _client(spine, name, owner=""):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO clients(name, oib, owner) VALUES(?,?,?)", (name, name, owner)
        )
    return cur.lastrowid


def _obligation(spine, client_id, kind, period, sent=0):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO obligations(client_id, kind, period) VALUES(?,?,?)",
            (client_id, kind, period),
        )
        oid = cur.lastrowid
        if sent:
            c.execute(
                "INSERT INTO obligation_status(obligation_id, sent) VALUES(?,1)", (oid,)
            )
    return oid


def test_build_digest_aggregates_sections(spine, cfg, monkeypatch):
    today = date(2026, 8, 1)
    monkeypatch.setattr(kalendar, "_today", lambda: today)
    monkeypatch.setattr(expiry, "_today", lambda: today)

    cid = _client(spine, "Alfa doo")
    with spine.write() as c:
        c.execute(
            "INSERT INTO deadlines(kind, rule, description) VALUES('PDV','monthly:20','PDV obrazac')"
        )
        c.execute(
            "INSERT INTO deadline_dates(kind, due, year) VALUES('PDV', ?, 2026)",
            ((today + timedelta(days=3)).isoformat(),),
        )
    _obligation(spine, cid, "PDV", "2026-08", sent=0)
    expiry.add(spine, cid, "osobna", "Osobna iskaznica", (today + timedelta(days=10)).isoformat())
    with spine.write() as c:
        c.execute(
            "INSERT INTO notifications(kind, body) VALUES('law_change','Izmjena Zakona o PDV-u')"
        )

    text = digest.build_digest(spine, cfg, now_fn=lambda: today)

    assert "Alfa doo" in text
    assert "rok" in text
    assert "obveza" in text
    assert "Izmjena Zakona o PDV-u" in text


def test_build_digest_empty_state(spine, cfg):
    text = digest.build_digest(spine, cfg, now_fn=lambda: date(2026, 8, 1))
    assert "Nema hitnih obveza" in text


def test_build_digest_eracun_only_suppresses_empty_state(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO notifications(kind, body) VALUES('eracun','novi e-racun')")
    text = digest.build_digest(spine, cfg, now_fn=lambda: date(2026, 8, 1))
    assert "Novi e-računi: 1" in text
    assert "Nema hitnih obveza" not in text


def test_build_digest_filters_by_worker(spine, cfg):
    today = date(2026, 8, 1)
    cid_ana = _client(spine, "Ana Klijent", owner="ana")
    cid_iva = _client(spine, "Iva Klijent", owner="iva")
    _obligation(spine, cid_ana, "PDV", "2026-08", sent=0)
    _obligation(spine, cid_iva, "PDV", "2026-08", sent=0)
    with spine.write() as c:
        c.execute(
            "INSERT INTO notifications(kind, body) VALUES('rss','Nova objava propisa')"
        )

    text = digest.build_digest(spine, cfg, worker="ana", now_fn=lambda: today)

    assert "Ana Klijent" in text
    assert "Iva Klijent" not in text
    assert "Nova objava propisa" in text


def test_deliver_returns_none_without_urls(cfg):
    assert cfg.apprise_urls == []
    assert digest.deliver(cfg, "subject", "body") == "none"


def test_workers_empty_when_no_users(spine):
    assert digest.workers(spine) == []


def test_workers_lists_usernames(spine):
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash) VALUES('ana','x')")
        c.execute("INSERT INTO users(username, pw_hash) VALUES('iva','x')")
    assert digest.workers(spine) == ["ana", "iva"]


def test_digest_job_office_wide_when_no_users(spine, cfg):
    digest.digest_job(spine, cfg)
    rows = spine.read().execute("SELECT * FROM notifications WHERE kind='digest'").fetchall()
    assert len(rows) == 1


def test_digest_job_one_per_worker(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash) VALUES('ana','x')")
        c.execute("INSERT INTO users(username, pw_hash) VALUES('iva','x')")
    digest.digest_job(spine, cfg)
    rows = spine.read().execute("SELECT * FROM notifications WHERE kind='digest'").fetchall()
    assert len(rows) == 2


def test_register_defaults_includes_digest(spine, cfg):
    from ragspine.ops import jobs

    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    names = {j.name for j in sched.jobs}
    assert "digest" in names
    job = next(j for j in sched.jobs if j.name == "digest")
    assert job.daily is True
    assert job.at_hour == cfg.digest_hour


def test_cli_digest_prints_report(tmp_path, monkeypatch, capsys):
    from ragspine.__main__ import main

    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    assert main(["digest"]) == 0
    out = capsys.readouterr().out
    assert "Jutarnji pregled" in out


def _seed_workers_and_law_changes(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,owner,industry,active) VALUES('Gostiona','1','ana','ugostiteljstvo',1)")
        c.execute("INSERT INTO clients(name,oib,owner,industry,active) VALUES('Gradnja','2','ivan','građevina',1)")
        for body in ("[ugostiteljstvo-turizam] Promjena na mint",
                     "[gradevina] Promjena na mpgi",
                     "[trgovina-proizvodnja-it] Promjena na mingo",
                     "[porezna-vijesti] Promjena na porezna",
                     "[place-statistika] Promjena na dzs"):
            c.execute("INSERT INTO notifications(kind,body,seen) VALUES('law_change',?,0)", (body,))


def test_digest_law_changes_filtered_by_worker_industry(spine, cfg):
    _seed_workers_and_law_changes(spine)
    ana = digest.build_digest(spine, cfg, worker="ana")
    # ana ima ugostiteljstvo klijenta → vidi ugostiteljstvo + univerzalne, NE građevinu/trgovinu
    assert "mint" in ana and "porezna" in ana and "dzs" in ana
    assert "mpgi" not in ana and "mingo" not in ana
    ivan = digest.build_digest(spine, cfg, worker="ivan")
    # ivan ima građevina (s dijakritikom) → vidi gradevina + univerzalne, NE ugostiteljstvo
    assert "mpgi" in ivan and "porezna" in ivan and "dzs" in ivan
    assert "mint" not in ivan and "mingo" not in ivan


def test_digest_office_wide_sees_all_law_changes(spine, cfg):
    _seed_workers_and_law_changes(spine)
    allw = digest.build_digest(spine, cfg, worker=None)
    for token in ("mint", "mpgi", "mingo", "porezna", "dzs"):
        assert token in allw
