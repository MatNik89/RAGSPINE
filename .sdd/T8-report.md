# T8 — VAULT move/rename detection

## Status: DONE

Commit: `c276250` — feat: VAULT content-hash reconciliation for moved/renamed NAS docs (T8)

## Test summary

`python -m pytest tests/ -q` → **421 passed, 1 skipped** (409 baseline + 12 new in tests/test_vault.py; the 1 skip is pre-existing, unrelated).

## What was built

- `ragspine/docs/vault.py` (new): `_file_sha` (re-exported from `ingest.py`), `resolve_scope(cfg, root)`
  (realpath+commonpath scoping under `nas_root`/`data_dir`, same pattern as `eracun._resolve_dest`),
  `scan_directory(spine, root, ingest_new=True)`, `vault_status(spine)`.
- `documents.file_sha TEXT` column via `_ensure_columns` in `core/spine.py`; set in
  `ingest.ingest_file()` (raw-file-bytes hash, kept distinct from the existing normalized-TEXT
  `sha256` dedup column, which is untouched).
- `POST /vault/scan` and `GET /vault/status` in `web/api.py`, both behind `require_user_web`.
- `tests/test_vault.py`: 12 tests, TDD (written failing first, confirmed ImportError before
  `vault.py` existed).

## Key behavior verified

`test_scan_move_preserves_chunks`: ingest a file, `os.rename` it into a new subdirectory, run
`scan_directory` — result reports 1 moved, `documents.id` for that doc is unchanged, `documents.path`
is updated to the new location, and the exact same `chunks.id` set still exists under that `doc_id`
(no delete+re-ingest). Also covered: pure rename (same dir) tagged "renamed" vs cross-dir "moved";
new file → ingested; same-path content change → "changed" + re-ingest; disappeared file with no
matching content elsewhere in the scan → soft-deleted (`stale=1`, row still present, not hard-deleted);
non-ingestable extensions skipped; backfill of `file_sha` for pre-existing rows; API root-scoping
(400 outside `nas_root`/`data_dir`) and auth (401 without token).

## Concerns (original, T8)

- ~~`changed` case re-ingests via `ingest_file`, which (per spec, "keep simple") inserts a new
  `documents` row rather than updating the old one in place — the old row for that path is left
  `stale=0` with stale content rather than being auto-superseded.~~ **Fixed in the follow-up round
  below.**
- Soft-delete only fires for documents whose `path` falls under the scanned `root` (added this
  scoping so scanning a subdirectory can't collateral-damage docs elsewhere in the vault) — this is
  a reasonable interpretation but wasn't explicit in the spec.

---

## Fix round 2 (coordinator review)

Commit: `<see below>` — fix: T8 vault stale-supersede on change + legacy file_sha backfill

Two IMPORTANT issues from coordinator review, both fixed:

**1. Changed content left a duplicate active row.** On "changed" (same path, new `file_sha`), the
old `documents` row stayed `stale=0` while `ingest_file` inserted a *new* row at the same path — two
active rows, stale content polluting retrieval. Fix: `ragspine/docs/vault.py`, in the `changed`
branch, after re-ingesting, `UPDATE documents SET stale=1 WHERE id=<old_id>` — only gated on
`ingest_new` (dry-run doesn't mutate). Added
`test_scan_changed_marks_old_row_stale_only_new_active`: verifies old row `stale=1`, new row
`stale=0`, and `retrieval.search` (freshness filter, default `True`) only surfaces the new `doc_id`.

**2. Legacy `file_sha IS NULL` rows excluded from move reconciliation.** `by_sha` was built only from
rows with a populated `file_sha`, so a legacy doc could in principle be excluded from move matching.
Fix: added an explicit backfill pass at the **top** of `scan_directory`, before `by_sha` is built —
for every DB row whose current `path` still exists on disk and has no `file_sha`, compute and store
it there. Added `test_scan_backfills_legacy_file_sha_then_detects_move`: insert a legacy row
(`file_sha` NULL) pointing at a real file, scan once (backfills, 0 moved), rename the file, scan
again → 1 moved/renamed, `doc_id` preserved, `path` updated.

Residual, honestly-stated limitation (not fixable by any backfill): a legacy doc whose file is moved
**before its very first scan ever** — i.e. its `file_sha` was never recorded while the file still sat
at the old path — has no historical identity to reconcile against once the old path is gone; that one
scan will still classify it as delete+new. Backfilling requires the file to exist somewhere to hash;
it can't hash a path that's already vanished. This is a fundamental, not an implementation, gap —
worth surfacing to operators as "run a scan before you reorganize, not just after."

**Deferred (MINOR, per coordinator instruction — not fixed):** two brand-new files with identical
content: the second is text-sha-deduped by `ingest_text` (returns `None`, no row inserted), so
`scan_directory` re-reports it as `"new"` on every subsequent scan (miscount, not data loss). Upgrade
path: track a per-scan "already reported new, still absent from documents" skip-set, or persist a
lightweight seen-content-hash ledger independent of `documents`.

### Test summary (fix round 2)

`python -m pytest tests/test_vault.py -q` → **14 passed** (12 baseline + 2 new).
`python -m pytest tests/ -q` → **423 passed, 1 skipped** (421 baseline + 2 new; skip is pre-existing, unrelated).
