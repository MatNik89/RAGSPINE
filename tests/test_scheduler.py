import datetime
import threading

from ragspine.config import Config
from ragspine.core.spine import Spine
from ragspine.ops.scheduler import Job, Scheduler


class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds=0, hours=0):
        self.t += datetime.timedelta(seconds=seconds, hours=hours)


def test_interval_job_runs_and_persists_lastrun(tmp_path):
    spine = Spine(str(tmp_path / "t.db"))
    clock = Clock(datetime.datetime(2026, 8, 1, 10, 0, 0))
    sched = Scheduler(spine, cfg=None, now_fn=clock)
    counter = {"n": 0}

    def fn(spine, cfg):
        counter["n"] += 1

    sched.register(Job(name="tick60", fn=fn, interval_s=60))

    assert sched.tick() == ["tick60"]
    assert counter["n"] == 1
    row = spine.read().execute(
        "SELECT value FROM memory WHERE user='scheduler' AND key='lastrun.tick60'"
    ).fetchone()
    assert row is not None
    assert row["value"] == clock.t.isoformat()

    # immediately again — interval not elapsed
    assert sched.tick() == []
    assert counter["n"] == 1

    clock.advance(seconds=61)
    assert sched.tick() == ["tick60"]
    assert counter["n"] == 2


def test_daily_job_runs_once_per_day_after_at_hour(tmp_path):
    spine = Spine(str(tmp_path / "t.db"))
    clock = Clock(datetime.datetime(2026, 8, 1, 6, 0, 0))
    sched = Scheduler(spine, cfg=None, now_fn=clock)
    counter = {"n": 0}

    def fn(spine, cfg):
        counter["n"] += 1

    sched.register(Job(name="digest", fn=fn, interval_s=0, daily=True, at_hour=7))

    assert sched.tick() == []
    assert counter["n"] == 0

    clock.t = datetime.datetime(2026, 8, 1, 7, 30, 0)
    assert sched.tick() == ["digest"]
    assert counter["n"] == 1

    clock.t = datetime.datetime(2026, 8, 1, 8, 0, 0)
    assert sched.tick() == []
    assert counter["n"] == 1

    clock.t = datetime.datetime(2026, 8, 2, 7, 30, 0)
    assert sched.tick() == ["digest"]
    assert counter["n"] == 2


def test_error_isolation(tmp_path):
    spine = Spine(str(tmp_path / "t.db"))
    clock = Clock(datetime.datetime(2026, 8, 1, 10, 0, 0))
    sched = Scheduler(spine, cfg=None, now_fn=clock)
    counter = {"n": 0}

    def bad(spine, cfg):
        raise RuntimeError("boom")

    def good(spine, cfg):
        counter["n"] += 1

    sched.register(Job(name="bad", fn=bad, interval_s=60))
    sched.register(Job(name="good", fn=good, interval_s=60))

    ran = sched.tick()
    assert "good" in ran
    assert counter["n"] == 1

    row = spine.read().execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE kind='scheduler_error'"
    ).fetchone()
    assert row["n"] == 1


def test_run_exits_promptly_on_stop_event(tmp_path):
    spine = Spine(str(tmp_path / "t.db"))
    clock = Clock(datetime.datetime(2026, 8, 1, 10, 0, 0))
    sched = Scheduler(spine, cfg=None, now_fn=clock)
    counter = {"n": 0}
    sched.register(Job(name="once", fn=lambda spine, cfg: counter.__setitem__("n", counter["n"] + 1), interval_s=60))

    stop_event = threading.Event()
    stop_event.set()  # already set: run() should tick once then exit immediately

    import time
    start = time.monotonic()
    sched.run(poll_s=0.05, stop_event=stop_event)
    elapsed = time.monotonic() - start

    assert counter["n"] == 1
    assert elapsed < 1.0


def test_apprise_urls_and_digest_hour(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGSPINE_APPRISE_URLS", "a://x,b://y")
    cfg = Config.from_env()
    assert cfg.apprise_urls == ["a://x", "b://y"]
    assert cfg.digest_hour == 7


def test_digest_hour_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGSPINE_DIGEST_HOUR", "9")
    assert Config.from_env().digest_hour == 9


def test_apprise_urls_default_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    assert Config.from_env().apprise_urls == []
