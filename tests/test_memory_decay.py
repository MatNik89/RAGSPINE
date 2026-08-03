from datetime import datetime, timedelta

from ragspine.core import memory
from ragspine.ops import jobs
from ragspine.ops.scheduler import Scheduler


def test_write_memory_sets_hot_score_and_access_count(spine):
    memory.write_memory(spine, "ana", "omiljeni_konto", "4000")
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "omiljeni_konto")
    ).fetchone()
    assert row["value"] == "4000"
    assert row["hot_score"] == 1.0
    assert row["access_count"] == 1


def test_write_memory_upserts_existing_key(spine):
    memory.write_memory(spine, "ana", "k", "v1")
    memory.write_memory(spine, "ana", "k", "v2")
    rows = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["value"] == "v2"


def test_touch_memory_bumps_hot_score(spine):
    memory.write_memory(spine, "ana", "k", "v")
    memory.touch_memory(spine, "ana", "k")
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchone()
    assert row["hot_score"] == 1.1
    assert row["access_count"] == 2


def test_touch_memory_caps_at_ten(spine):
    memory.write_memory(spine, "ana", "k", "v")
    for _ in range(200):
        memory.touch_memory(spine, "ana", "k")
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchone()
    assert row["hot_score"] == 10.0


def test_get_memory_returns_value_and_touches(spine):
    memory.write_memory(spine, "ana", "k", "hello")
    value = memory.get_memory(spine, "ana", "k")
    assert value == "hello"
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchone()
    assert row["hot_score"] == 1.1
    assert row["access_count"] == 2


def test_get_memory_missing_key_returns_none(spine):
    assert memory.get_memory(spine, "ana", "nope") is None


def test_decay_all_zero_days_unchanged(spine):
    now = datetime(2026, 1, 15, 12, 0, 0)
    memory.write_memory(spine, "ana", "k", "v", now_fn=lambda: now)
    updated = memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: now)
    assert updated == 1
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchone()
    assert abs(row["hot_score"] - 1.0) < 1e-6


def test_decay_all_one_half_life_halves_score(spine):
    now = datetime(2026, 1, 1, 0, 0, 0)
    memory.write_memory(spine, "ana", "k", "v", now_fn=lambda: now)
    later = now + timedelta(days=14)
    updated = memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: later)
    assert updated == 1
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchone()
    assert abs(row["hot_score"] - 0.5) < 0.01


def test_decay_all_two_half_lives_quarters_score(spine):
    now = datetime(2026, 1, 1, 0, 0, 0)
    memory.write_memory(spine, "ana", "k", "v", now_fn=lambda: now)
    later = now + timedelta(days=28)
    memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: later)
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchone()
    assert abs(row["hot_score"] - 0.25) < 0.01


def test_decay_floors_at_point_zero_one(spine):
    now = datetime(2026, 1, 1, 0, 0, 0)
    memory.write_memory(spine, "ana", "k", "v", now_fn=lambda: now)
    much_later = now + timedelta(days=3650)
    memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: much_later)
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user=? AND key=?", ("ana", "k")
    ).fetchone()
    assert row["hot_score"] >= 0.01
    assert row["hot_score"] > 0


def test_hot_memories_sorted_desc_per_user(spine):
    memory.write_memory(spine, "ana", "cold", "c")
    memory.write_memory(spine, "ana", "hot", "h")
    memory.touch_memory(spine, "ana", "hot")
    memory.touch_memory(spine, "ana", "hot")
    memory.write_memory(spine, "bruno", "other", "o")
    memory.touch_memory(spine, "bruno", "other")
    memory.touch_memory(spine, "bruno", "other")
    memory.touch_memory(spine, "bruno", "other")

    result = memory.hot_memories(spine, "ana", limit=10)
    assert [r["key"] for r in result] == ["hot", "cold"]
    assert result[0]["hot_score"] > result[1]["hot_score"]
    assert result[0]["value"] == "h"


def test_hot_memories_respects_min_score(spine):
    now = datetime(2026, 1, 1, 0, 0, 0)
    memory.write_memory(spine, "ana", "k", "v", now_fn=lambda: now)
    much_later = now + timedelta(days=3650)
    memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: much_later)
    result = memory.hot_memories(spine, "ana", limit=10, min_score=0.5)
    assert result == []


def test_forget_cold_deletes_below_threshold(spine):
    now = datetime(2026, 1, 1, 0, 0, 0)
    much_later = now + timedelta(days=3650)
    memory.write_memory(spine, "ana", "stale", "v", now_fn=lambda: now)
    memory.write_memory(spine, "ana", "fresh", "v2", now_fn=lambda: much_later)
    memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: much_later)

    deleted = memory.forget_cold(spine, threshold=0.05)
    assert deleted == 1
    remaining = {r["key"] for r in spine.read().execute("SELECT key FROM memory").fetchall()}
    assert remaining == {"fresh"}


def test_decay_all_skips_scheduler_namespace(spine):
    # T13 hardening: scheduler persists lastrun.{job} under user='scheduler' —
    # decay_all must never touch it (harmless here since it only lowers
    # hot_score, but the namespace should stay untouched on principle).
    now = datetime(2026, 1, 1, 0, 0, 0)
    memory.write_memory(spine, "scheduler", "lastrun.digest", "2026-01-01", now_fn=lambda: now)
    much_later = now + timedelta(days=3650)
    memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: much_later)
    row = spine.read().execute(
        "SELECT * FROM memory WHERE user='scheduler' AND key='lastrun.digest'"
    ).fetchone()
    assert row["hot_score"] == 1.0


def test_forget_cold_never_deletes_scheduler_namespace(spine):
    now = datetime(2026, 1, 1, 0, 0, 0)
    much_later = now + timedelta(days=3650)
    memory.write_memory(spine, "scheduler", "lastrun.digest", "2026-01-01", now_fn=lambda: now)
    memory.write_memory(spine, "ana", "stale", "v", now_fn=lambda: now)
    memory.decay_all(spine, half_life_days=14.0, now_fn=lambda: much_later)

    # scheduler row was never decayed (hot_score still 1.0, above threshold),
    # but even if it HAD decayed below threshold, forget_cold must not delete it.
    with spine.write() as c:
        c.execute("UPDATE memory SET hot_score=0.0 WHERE user='scheduler'")

    deleted = memory.forget_cold(spine, threshold=0.05)
    assert deleted == 1  # only ana's stale row
    remaining = {r["user"] for r in spine.read().execute("SELECT user FROM memory").fetchall()}
    assert remaining == {"scheduler"}


def test_memory_decay_job_calls_decay_all(spine, cfg, monkeypatch):
    called = {}

    def fake_decay_all(s, **kw):
        called["ok"] = True
        return 0

    monkeypatch.setattr(memory, "decay_all", fake_decay_all)
    jobs.memory_decay_job(spine, cfg)
    assert called.get("ok") is True


def test_memory_decay_job_registered_in_defaults(spine, cfg):
    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    names = {j.name for j in sched.jobs}
    assert "memory_decay" in names


def test_register_defaults_registers_all_jobs(spine, cfg):
    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    names = {j.name for j in sched.jobs}
    assert names == {
        "watchlist", "imap", "deadlines", "expiry", "obveze", "stale", "health",
        "digest", "reminders_dump", "memory_decay", "memory_distill", "rokovi", "folders_sync",
    }
