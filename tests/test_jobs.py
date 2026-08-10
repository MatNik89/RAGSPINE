from datetime import date, timedelta

from atlas.business import expiry, kalendar, obveze
from atlas.ops import jobs
from atlas.ops.scheduler import Scheduler


def _client(spine, name="Alfa", pdv=""):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO clients(name, oib, pdv_status) VALUES (?,?,?)", (name, name, pdv)
        )
    return cur.lastrowid


def test_deadlines_job_creates_notifications_and_dedupes(spine, cfg, monkeypatch):
    kalendar.seed(spine, 2026)
    monkeypatch.setattr(kalendar, "_today", lambda: date(2026, 2, 25))

    jobs.deadlines_job(spine, cfg)
    rows = spine.read().execute("SELECT * FROM notifications WHERE kind='deadline'").fetchall()
    assert len(rows) > 0
    first_count = len(rows)

    jobs.deadlines_job(spine, cfg)
    rows2 = spine.read().execute("SELECT * FROM notifications WHERE kind='deadline'").fetchall()
    assert len(rows2) == first_count


def test_expiry_job_creates_notification_and_dedupes(spine, cfg, monkeypatch):
    cid = _client(spine)
    today = date(2026, 1, 1)
    monkeypatch.setattr(expiry, "_today", lambda: today)
    expires = (today + timedelta(days=20)).isoformat()
    expiry.add(spine, cid, "osobna", "Osobna iskaznica", expires)

    jobs.expiry_job(spine, cfg)
    rows = spine.read().execute("SELECT * FROM notifications WHERE kind='expiry'").fetchall()
    assert len(rows) == 1

    jobs.expiry_job(spine, cfg)
    rows2 = spine.read().execute("SELECT * FROM notifications WHERE kind='expiry'").fetchall()
    assert len(rows2) == 1


def test_obveze_job_seeds_current_period_idempotently(spine, cfg, monkeypatch):
    _client(spine, "Alfa", "u sustavu pdv")
    monkeypatch.setattr(jobs, "_period_now", lambda: "2026-07")

    jobs.obveze_job(spine, cfg)
    rows = obveze.list_period(spine, "PDV", "2026-07")
    assert len(rows) == 1

    jobs.obveze_job(spine, cfg)
    rows2 = obveze.list_period(spine, "PDV", "2026-07")
    assert len(rows2) == 1


def test_imap_job_skips_when_unconfigured(spine, cfg):
    assert cfg.imap_host == ""
    jobs.imap_job(spine, cfg)  # must not raise / touch imap_state
    assert spine.read().execute("SELECT * FROM imap_state").fetchone() is None


def test_watchlist_job_smoke(spine, cfg):
    jobs.watchlist_job(spine, cfg)


def test_stale_job_smoke(spine, cfg):
    jobs.stale_job(spine, cfg)


def test_health_job_smoke(spine, cfg):
    jobs.health_job(spine, cfg)


def test_register_defaults_registers_all_jobs(spine, cfg):
    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    names = {j.name for j in sched.jobs}
    assert names == {
        "watchlist", "imap", "deadlines", "expiry", "obveze", "stale", "health",
        "digest", "reminders_dump", "memory_decay", "memory_distill", "rokovi", "folders_sync", "backup",
        "power", "scheduled_tasks",
    }
