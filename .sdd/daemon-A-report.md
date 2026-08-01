# Daemon Task A — scheduler engine + CLI + config

## Status
DONE.

## What was built
- `ragspine/config.py`: `apprise_urls: list[str]` (env `RAGSPINE_APPRISE_URLS`, CSV) and
  `digest_hour: int` (env `RAGSPINE_DIGEST_HOUR`, default 7) added to `Config` + `from_env`.
- `ragspine/ops/scheduler.py` (new): `Job` dataclass (name, fn(spine,cfg), interval_s, daily,
  at_hour), `Scheduler` (register/tick/run), `build_default_scheduler(spine, cfg)` which
  lazily wires `ragspine.ops.jobs.register_defaults` (Task B) via try/except ImportError —
  currently a no-op since `ops/jobs.py` doesn't exist yet.
  - All time reads go through `self._now()` (injectable `now_fn`, defaults to
    `datetime.datetime.now`) — no real sleeps needed in tests.
  - Last-run persistence uses the existing `memory` table: `user='scheduler'`,
    `key=f'lastrun.{job.name}'`, ISO-format value, upserted via
    `ON CONFLICT(user,key) DO UPDATE`.
  - `tick()` runs jobs sequentially in the calling thread (documented in the module
    docstring — jobs are I/O bound and infrequent, no per-job threading needed). A failing
    job is caught, logged via `logging.warning`, and recorded as a `notifications` row with
    `kind='scheduler_error'`; it does not stop remaining jobs. Only successfully-completed
    jobs are included in the returned list.
  - `run(poll_s=30, stop_event=None)` is the blocking daemon loop: `tick()` then
    `stop_event.wait(poll_s)` — bounded wait, no busy-spin, exits when the event fires
    (checked after each tick, so a pre-set event still lets the loop tick once — matches
    the CLI's "run to completion of at least one cycle before shutdown" behavior).
- `ragspine/__main__.py`: new `daemon` subcommand — `init_spine`, `build_default_scheduler`,
  installs SIGINT/SIGTERM handlers that set a `threading.Event`, prints startup/shutdown
  lines, calls `sched.run(poll_s=30, stop_event=...)`, returns 0.

## Tests (tests/test_scheduler.py, TDD, all new — no real sleeps, injectable clock)
1. Interval job: runs once, persists ISO lastrun in `memory`; immediate re-tick is a no-op;
   +61s tick runs again.
2. Daily+at_hour: 06:00 no-op, 07:30 runs, same-day 08:00 no-op, next-day 07:30 runs again.
3. Error isolation: one job raises, the other still runs and increments its counter;
   a `notifications` row with `kind='scheduler_error'` is written; no exception propagates.
4. `run()` with a pre-set `stop_event` and `poll_s=0.05` — ticks exactly once and returns
   in well under 1s.
5. Config: `RAGSPINE_APPRISE_URLS="a://x,b://y"` → `["a://x","b://y"]`; `digest_hour`
   defaults to 7 and honors `RAGSPINE_DIGEST_HOUR` override; empty-URL default is `[]`.

## Full suite
`python -m pytest tests/ -q` → **287 passed, 1 skipped** (280 baseline + 7 new
`test_scheduler.py` tests, no regressions, ~20s).

## Concerns / notes for Task B & C
- `build_default_scheduler` registers nothing yet — Task B must add `ragspine/ops/jobs.py`
  with `register_defaults(sched)`.
- `digest_hour` config knob is present but unused until Task C wires a daily digest job.
- `apprise_urls` config knob is present but unused until a notification-delivery job
  consumes it (currently only `ops/health.py`'s `_alert` uses apprise, with a `ponytail:`
  comment noting it has no configured targets — Task B/C should point it at
  `cfg.apprise_urls`).
- `Job.fn` signature is always `(spine, cfg)` — the no-args option from the task
  description was not implemented since a single consistent signature is simpler for
  Task B/C job authors (ponytail: one calling convention, not two).
- No new dependencies; stdlib only (`datetime`, `threading`, `logging`, `dataclasses`,
  `collections.abc.Callable`).
