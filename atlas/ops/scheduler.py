"""Stdlib interval/daily scheduler for background jobs. Jobs run sequentially
per tick (I/O bound, infrequent) — no per-job threads."""
import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_USER = "scheduler"


@dataclass
class Job:
    name: str
    fn: Callable  # fn(spine, cfg) -> None
    interval_s: int
    daily: bool = False
    at_hour: int | None = None


class Scheduler:
    def __init__(self, spine, cfg, now_fn=None):
        self.spine = spine
        self.cfg = cfg
        self._now = now_fn or datetime.datetime.now
        self.jobs: list[Job] = []

    def register(self, job: Job) -> None:
        self.jobs.append(job)

    def _get_last_run(self, name: str) -> datetime.datetime | None:
        row = self.spine.read().execute(
            "SELECT value FROM memory WHERE user=? AND key=?", (_USER, f"lastrun.{name}")
        ).fetchone()
        return datetime.datetime.fromisoformat(row["value"]) if row else None

    def _set_last_run(self, name: str, dt: datetime.datetime) -> None:
        with self.spine.write() as c:
            c.execute(
                """INSERT INTO memory(user,key,value) VALUES(?,?,?)
                   ON CONFLICT(user,key) DO UPDATE SET value=excluded.value""",
                (_USER, f"lastrun.{name}", dt.isoformat()),
            )

    def _should_run(self, job: Job, last_run: datetime.datetime | None) -> bool:
        now = self._now()
        if job.daily:
            if last_run is not None and last_run.date() >= now.date():
                return False
            return job.at_hour is None or now.hour >= job.at_hour
        return last_run is None or (now - last_run).total_seconds() >= job.interval_s

    def tick(self) -> list[str]:
        ran = []
        for job in self.jobs:
            last_run = self._get_last_run(job.name)
            if not self._should_run(job, last_run):
                continue
            now = self._now()
            try:
                job.fn(self.spine, self.cfg)
            except Exception as e:
                logger.warning("scheduler job %s failed: %s", job.name, e)
                with self.spine.write() as c:
                    c.execute(
                        "INSERT INTO notifications(kind, body) VALUES(?,?)",
                        ("scheduler_error", f"{job.name}: {e}"),
                    )
            else:
                ran.append(job.name)
            self._set_last_run(job.name, now)
        return ran

    def run(self, poll_s=30, stop_event=None) -> None:
        """Blocking loop: tick(), then wait poll_s on stop_event. Bounded, no
        busy-spin. Exits once stop_event is set."""
        if stop_event is None:
            import threading
            stop_event = threading.Event()
        while True:
            self.tick()
            if stop_event.wait(poll_s):
                return


def build_default_scheduler(spine, cfg) -> Scheduler:
    sched = Scheduler(spine, cfg)
    try:
        from atlas.ops import jobs
        jobs.register_defaults(sched)
    except ImportError:
        pass
    return sched
