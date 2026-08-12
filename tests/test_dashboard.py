from datetime import date

from fastapi.testclient import TestClient

from atlas.business import dashboard, expiry, deadline_calendar, monthly
from atlas.rag import pipeline
from atlas.web.api import create_app
from atlas.web.deps import add_user


def _seed(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, oib, pdv_status, active) VALUES ('Alfa', '1', 'u sustavu pdv', 1)")
        c.execute("INSERT INTO clients(name, oib, pdv_status, active) VALUES ('Beta', '2', 'u sustavu pdv', 1)")
        c.execute("INSERT INTO clients(name, oib, active) VALUES ('Gama', '3', 0)")
        c.execute("INSERT INTO notifications(kind, body, seen) VALUES ('law_change', 'Nova stopa PDV-a', 0)")
        c.execute("INSERT INTO notifications(kind, body, seen) VALUES ('rss', 'Neka vijest', 1)")
        c.execute("INSERT INTO notes(client_id, author, body) VALUES (1, 'ana', 'Bilješka o Alfi')")


def _seed_july(spine):
    _seed(spine)
    with spine.write() as c:
        c.execute("UPDATE notifications SET at='2026-07-15 10:00:00'")


def test_dashboard_stats(spine):
    _seed(spine)
    s = dashboard.stats(spine)
    assert set(s) == {"active_clients", "deadlines_this_week", "top_clients",
                       "unseen_notifications", "peer_disagreements"}
    assert s["active_clients"] == 2
    assert s["unseen_notifications"] == 1
    assert isinstance(s["deadlines_this_week"], int)
    assert isinstance(s["top_clients"], list)


def test_dashboard_stats_empty_does_not_crash(spine):
    s = dashboard.stats(spine)
    assert s["active_clients"] == 0
    assert s["top_clients"] == []


def test_monthly_re_matches_hr_variants():
    for q in [
        "što sve moram ovaj mjesec?",
        "što moram?",
        "što mi još moram",
        "obaveze ovaj mjesec",
        "mjesečni pregled",
        "što moram ovaj mjesec",
    ]:
        assert monthly.MONTHLY_RE.search(q), q


def test_monthly_re_matches_ascii_and_diacritic_jos():
    assert monthly.MONTHLY_RE.search("što još moram ovaj mjesec")
    assert monthly.MONTHLY_RE.search("sto jos moram ovaj mjesec")


def test_period_bounds():
    assert monthly._period_bounds("2026-07") == ("2026-07-01", "2026-08-01")
    assert monthly._period_bounds("2026-12") == ("2026-12-01", "2027-01-01")
    assert monthly._period_bounds("2026-02") == ("2026-02-01", "2026-03-01")


def test_overview_deadlines_use_requested_period_not_today(spine, monkeypatch):
    deadline_calendar.seed(spine, 2026)
    # today/_period_now anchored to a DIFFERENT month than the requested period
    monkeypatch.setattr(monthly, "_period_now", lambda: "2026-01")
    ov = monthly.overview(spine, "2026-07")
    assert ov["deadlines"], "deadlines must be period-scoped, not today-window-scoped"
    assert all(d["due"].startswith("2026-07") for d in ov["deadlines"])


def test_overview_watch_changes_excludes_next_month_bleed(spine):
    with spine.write() as c:
        c.execute(
            "INSERT INTO notifications(kind, body, at) VALUES ('law_change', 'ožujska vijest', '2026-03-02 09:00:00')"
        )
    ov = monthly.overview(spine, "2026-02")
    assert ov["watch_changes"] == []


def test_overview_has_all_keys_and_unsent_client(spine, monkeypatch):
    _seed_july(spine)
    monkeypatch.setattr(deadline_calendar, "_today", lambda: date(2026, 7, 5))
    monkeypatch.setattr(expiry, "_today", lambda: date(2026, 7, 5))
    deadline_calendar.seed(spine, 2026)
    cid = spine.read().execute("SELECT id FROM clients WHERE name='Alfa'").fetchone()["id"]
    expiry.add(spine, cid, "osobna", "Osobna iskaznica", "2026-08-01")

    ov = monthly.overview(spine, "2026-07")
    assert set(ov) == {"period", "deadlines", "unsent", "expiring", "watch_changes", "recent_notes"}
    assert ov["period"] == "2026-07"
    assert any(r["client"] == "Alfa" for r in ov["unsent"])
    assert len(ov["deadlines"]) > 0
    assert len(ov["watch_changes"]) == 2
    assert len(ov["recent_notes"]) == 1

    text = monthly.format_overview(ov)
    assert isinstance(text, str)
    assert "Alfa" in text or "2026-07" in text


def test_api_dashboard_and_monthly_require_auth(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    assert c.get("/dashboard", follow_redirects=False).status_code in (401, 303)
    assert c.get("/monthly", follow_redirects=False).status_code in (401, 303)


def test_api_dashboard_and_monthly(spine, cfg):
    _seed_july(spine)
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}

    r = c.get("/dashboard", headers=headers)
    assert r.status_code == 200
    assert r.json()["active_clients"] == 2

    r2 = c.get("/monthly?period=2026-07", headers=headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["period"] == "2026-07"
    assert "text" in body and "Alfa" in body["text"] or any(u["client"] == "Alfa" for u in body["unsent"])


def test_pipeline_monthly_intent(spine, cfg, monkeypatch):
    _seed(spine)
    monkeypatch.setattr(monthly, "_period_now", lambda: "2026-07")
    monkeypatch.setattr(deadline_calendar, "_today", lambda: date(2026, 7, 5))
    r = pipeline.answer(spine, cfg, "što sve moram ovaj mjesec?", "ana", llm=None)
    assert r["lane"] == "monthly"
    assert r["confidence"] == 1.0
    assert isinstance(r["answer"], str) and r["answer"]


def test_home_data_has_orientation(spine, tmp_path):
    import os
    from atlas.business import dashboard, folders, folder_scan
    from atlas.config import Config
    old = dict(os.environ)
    os.environ.update({"ATLAS_DATA_DIR": str(tmp_path / "data"),
                       "ATLAS_MOUNT_ROOTS": str(tmp_path / "share")})
    try:
        cfg = Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)
    kl = tmp_path / "share" / "KLIJENTI"; (kl / "A").mkdir(parents=True)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    folder_scan.scan(spine, cfg, fid)
    data = dashboard.home_data(spine)
    assert "orientation" in data
    assert data["orientation"]["folders"][0]["role"] == "klijenti"
    assert data["orientation"]["folders"][0]["scan"]["n_subdirs"] == 1
