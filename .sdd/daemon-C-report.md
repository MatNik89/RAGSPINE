# Daemon Task C — Morning Digest

## Status: DONE

## Deliverables
- `ragspine/ops/digest.py` (new): `build_digest(spine, cfg, worker=None, now_fn=None) -> str`,
  `workers(spine) -> list[str]`, `deliver(cfg, subject, body) -> str` ("apprise"/"none"/"error"),
  `digest_job(spine, cfg)`.
- `ragspine/ops/jobs.py`: `register_defaults` now also registers job `"digest"`
  (`daily=True, at_hour=sched.cfg.digest_hour`) — 8 jobs total.
- `ragspine/__main__.py`: new `ragspine digest` subcommand prints the office-wide
  digest text (worker=None) and returns 0.
- `tests/test_digest.py` (new, 10 tests): build_digest aggregation, empty state,
  owner-scoped filtering (unsent obligations + expiring docs; deadlines/law-changes
  stay office-wide), deliver() no-urls path, workers(), digest_job (no-users vs
  2-users → 1 vs 2 `kind='digest'` notifications), register_defaults includes
  "digest", CLI smoke test.
- `tests/test_jobs.py`: updated `test_register_defaults_registers_all_jobs`
  expected set from 7 to 8 job names (adds "digest").

## Design notes
- `deliver()` checks `cfg.apprise_urls` truthiness before calling
  `optional.need("apprise", ...)` — avoids registering a false "missing feature"
  entry when the office simply hasn't configured apprise. Per Task A review note,
  each URL is `.strip()`-ped before `app.add()`.
- `deliver()` wraps the apprise call in try/except → "error" so a delivery job
  never crashes/hangs the scheduler tick.
- Owner-scoping: `clients.owner` used to filter unsent obligations (direct SQL
  join, since `obveze.list_period` doesn't expose owner/client_id) and expiring
  docs (post-filter by client_id set). Deadlines (office-wide tax calendar) and
  law/rss notifications are never owner-filtered per spec.
- `digest_job` inserts one `notifications(kind='digest')` row per worker (or one
  office-wide row if no users exist) whenever `deliver()` didn't return
  "apprise" — no dedup, since the scheduler's own last-run tracking already
  prevents re-running the job same day.

## Test command
`python -m pytest tests/ -q` → **305 passed, 1 skipped** (was 295 baseline + 10 new digest tests).

## Concerns
- None blocking. `deliver()`'s real apprise path (`Apprise()`, `.add()`,
  `.notify()`) is exercised only via the no-urls short-circuit in tests — no
  apprise package installed/mocked here, so the actual network-call code path
  is unverified beyond code review (matches the task's "no real network/apprise
  in tests" constraint).
