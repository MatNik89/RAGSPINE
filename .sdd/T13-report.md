# T13 — FSRS-lite memory decay

## Status
DONE. TDD (tests first, red -> green), full suite green.

## What was built
- `ragspine/core/spine.py`: `_ensure_columns(memory, {hot_score REAL DEFAULT 1.0,
  last_access TEXT DEFAULT (datetime('now')), access_count INTEGER DEFAULT 0})`.
- `ragspine/core/memory.py` (new): `write_memory` (upsert, resets hot_score=1.0,
  access_count+1), `touch_memory` (+0.1 hot_score capped at 10.0, access_count+1),
  `get_memory` (read + touch), `decay_all` (exponential forgetting curve,
  `hot_score *= exp(-ln2 * days_since_last_access / half_life)`, floor 0.01,
  `now_fn` injectable for deterministic tests), `hot_memories` (per-user,
  hot_score DESC, `min_score` filter), `forget_cold` (DELETE below threshold,
  returns count deleted; not wired into the default job — see concerns).
- `ragspine/ops/jobs.py`: `memory_decay_job(spine, cfg)` calling `memory.decay_all(spine)`,
  registered daily at_hour=4 in `register_defaults`. Bumped job-count assertion
  in `tests/test_jobs.py`.
- `ragspine/web/api.py`: `GET /memory/hot`, `POST /memory` (MemoryBody{key,value}),
  `GET /memory/{key}` (404 if missing) — all behind `require_user_web`.

## Tests
- `tests/test_memory_decay.py` (24 cases): write/touch/cap/decay-math
  (0d unchanged, 14d≈0.5, 28d≈0.25, floor 0.01), get_memory touches,
  hot_memories sort + per-user isolation + min_score, forget_cold count,
  memory_decay_job wiring + registration.
- `tests/test_memory_api.py` (4 cases): roundtrip, 404, hot sort via HTTP, auth-required.
- `tests/test_jobs.py`: job-count assertion bumped to include `memory_decay`.

## Concerns
- The scheduler itself stores its own `lastrun.<job>` bookkeeping in the same
  `memory` table (user="scheduler"). `decay_all` will harmlessly decay those
  rows' `hot_score` too (nothing reads it), but I deliberately did **not** wire
  `forget_cold` into `memory_decay_job` — those scheduler rows are never
  touched via `touch_memory`, so on a long-lived deployment they'd eventually
  cross a forget threshold and get deleted, causing spurious job reruns.
  `forget_cold` is exposed and tested but left as an opt-in the operator (or a
  future task) can wire in explicitly, scoped away from `user="scheduler"`.
- `write_memory`/`touch_memory` reuse `spine.write()` per call (small,
  consistent with the rest of the codebase); `decay_all` iterates all rows in
  one transaction — fine at current table sizes, would want batching only at
  much larger scale.
