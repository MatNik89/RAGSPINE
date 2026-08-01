# T11 — Knowledge Versioning Lifecycle

## Status: DONE

## What was built
- `ragspine/core/spine.py`: `documents` gains `status TEXT DEFAULT 'active'`,
  `supersedes INTEGER`, `version INTEGER DEFAULT 1` via `_ensure_columns`
  (idempotent ALTER, reaches deployed DBs).
- `ragspine/rag/versioning.py` (new): `set_status`, `supersede`,
  `promote_draft`, `stage_draft`, `version_history`, `active_version`.
  `supersede` does old->superseded + new->active/supersedes/version+1 + audit
  row in a single `spine.write()` transaction (raw `audit_log` INSERT used
  instead of `spine.audit()` to avoid re-entering the non-reentrant write
  lock).
- `ragspine/rag/retrieval.py`: freshness SQL extended with
  `AND (d.status IS NULL OR d.status='active')` alongside existing
  stale/valid_until conditions — NULL (legacy) status stays retrievable.
- `ragspine/web/watchlist.py` `check_source`: after a detected change is
  re-ingested, looks up the prior `status='active'` document for the same
  `source_url` and calls `versioning.supersede`; wrapped in try/except with a
  raw-SQL fallback so a versioning failure can never break the watch run.
- `ragspine/web/api.py`: `POST /knowledge/{doc_id}/status`,
  `POST /knowledge/supersede`, `GET /knowledge/{doc_id}/versions`,
  `POST /knowledge/{doc_id}/promote`, all behind `require_user_web`.

## Tests (TDD)
`tests/test_versioning.py`, 9 new tests, written failing first (import error
on `ragspine.rag.versioning`, confirmed red), then implementation added until
green: set_status validation, supersede (both rows survive, statuses/version/
supersedes correct), retrieval excludes superseded + draft, promote_draft
requires draft, version_history walks a 3-deep chain from any point in it,
NULL/legacy status back-compat, active_version, watchlist re-ingest
supersedes prior version, full API round-trip with a bearer token.

## Full suite
`python -m pytest tests/ -q` → 444 passed, 1 skipped (pre-existing embed-model
skip), 0 failed. Baseline 435 + 9 new = 444.

## Concerns / non-blocking
- `version_history` walks parent/child pointers with no cycle guard —
  acceptable since `supersede` is the only writer of `supersedes` and always
  moves strictly forward; not adversarial-input-hardened (ponytail: add a
  visited-set guard if `supersedes` ever becomes externally settable).
- Watchlist supersede match is by `source_url` equality only (no fuzzy/URL
  normalization) — matches existing codebase conventions elsewhere.
