# Daemon Task B — jobs.py report

## Status
DONE.

## What was built
`ragspine/ops/jobs.py`: 7 scheduler jobs wrapping existing doers (watchlist,
imap, deadlines, expiry, obveze, stale, health), each `fn(spine, cfg) ->
None`, plus `register_defaults(sched)` registering all 7 with the intervals
specified in the task (watchlist 1h, imap 5min, deadlines/expiry daily@7,
obveze/stale daily@6, health 15min). `_period_now()` factors the current
period for testability; `_notify_once()` factors the dedupe-by-body-in-
last-7-days check shared by deadlines_job and expiry_job.

digest job intentionally NOT added (Task C wires it in).

## Tests
`tests/test_jobs.py`, 8 new tests: dedupe for deadlines/expiry (run twice,
count stable), obveze idempotency, imap skip-when-unconfigured, three job
smoke tests on a fresh spine, and register_defaults job-name-set assertion.
All deterministic — no real network/sleep (monkeypatched `_today`/
`_period_now`, injected fakes where the underlying doer already supports it).

## Test summary
`python -m pytest tests/ -q` → 295 passed, 1 skipped (287 baseline + 8 new).

## Concerns
None blocking. Note for Task C: `register_defaults` lives in
`ragspine/ops/jobs.py` and does not import digest — wire digest either by
adding a call inside `register_defaults` or registering it separately from
the daemon entrypoint, per the task's own instruction.
