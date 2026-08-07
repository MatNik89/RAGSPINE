import json
import os
from datetime import date, timedelta

from atlas.business import expiry, kalendar
from atlas.ops import jobs, reminders_dump
from atlas.ops.scheduler import Scheduler

_TODAY = date(2026, 8, 1)


def _client(spine, name="Alfa"):
    with spine.write() as c:
        cur = c.execute("INSERT INTO clients(name, oib) VALUES(?,?)", (name, name))
    return cur.lastrowid


def _seed(spine, monkeypatch):
    monkeypatch.setattr(kalendar, "_today", lambda: _TODAY)
    monkeypatch.setattr(expiry, "_today", lambda: _TODAY)
    with spine.write() as c:
        c.execute(
            "INSERT INTO reminders(user, body, due, done) VALUES('ana','Plati PDV',?,0)",
            ((_TODAY + timedelta(days=5)).isoformat(),),
        )
    cid = _client(spine)
    expiry.add(spine, cid, "osobna", "Osobna iskaznica", (_TODAY + timedelta(days=10)).isoformat())


def test_dump_writes_json_with_all_sections(spine, cfg, monkeypatch, tmp_path):
    _seed(spine, monkeypatch)
    cfg.nas_root = str(tmp_path / "nas")

    result = reminders_dump.dump(spine, cfg, now_fn=lambda: _TODAY)

    assert result["count"] > 0
    assert result["path"] == str(tmp_path / "nas" / "reminders.json")
    with open(result["path"], encoding="utf-8") as f:
        data = json.load(f)
    assert data["note"]
    assert "generated" in data
    assert len(data["reminders"]) == 1
    assert data["reminders"][0]["body"] == "Plati PDV"
    assert len(data["istek"]) == 1
    assert isinstance(data["rokovi"], list)


def test_dump_ignores_done_reminders_and_far_future(spine, cfg, monkeypatch, tmp_path):
    _seed(spine, monkeypatch)
    cfg.nas_root = str(tmp_path / "nas")
    with spine.write() as c:
        c.execute(
            "INSERT INTO reminders(user, body, due, done) VALUES('ana','Vec gotovo',?,1)",
            ((_TODAY + timedelta(days=3)).isoformat(),),
        )
        c.execute(
            "INSERT INTO reminders(user, body, due, done) VALUES('ana','Daleko',?,0)",
            ((_TODAY + timedelta(days=90)).isoformat(),),
        )

    result = reminders_dump.dump(spine, cfg, now_fn=lambda: _TODAY)
    with open(result["path"], encoding="utf-8") as f:
        data = json.load(f)
    bodies = {r["body"] for r in data["reminders"]}
    assert bodies == {"Plati PDV"}


def test_dump_overwrites_atomically_second_run(spine, cfg, monkeypatch, tmp_path):
    _seed(spine, monkeypatch)
    cfg.nas_root = str(tmp_path / "nas")

    r1 = reminders_dump.dump(spine, cfg, now_fn=lambda: _TODAY)
    r2 = reminders_dump.dump(spine, cfg, now_fn=lambda: _TODAY)

    assert r1["path"] == r2["path"]
    assert not os.path.exists(r1["path"] + ".tmp")
    with open(r2["path"], encoding="utf-8") as f:
        json.load(f)  # still valid json after 2nd write


def test_dump_falls_back_to_data_dir_when_no_nas_root(spine, cfg):
    assert cfg.nas_root == ""
    result = reminders_dump.dump(spine, cfg, now_fn=lambda: _TODAY)
    assert result["path"] == os.path.join(cfg.data_dir, "reminders.json")


def test_reminders_dump_job_registered_in_defaults(spine, cfg):
    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    names = {j.name for j in sched.jobs}
    assert "reminders_dump" in names
    job = next(j for j in sched.jobs if j.name == "reminders_dump")
    assert job.interval_s == 3600
