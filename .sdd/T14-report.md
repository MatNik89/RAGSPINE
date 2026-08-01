# T14 — SOP editorial workflow

Status: DONE
Commit: d9993c4 (branch build/v2-tiers)
Full suite: `python -m pytest tests/ -q` -> 495 passed, 1 skipped (baseline 476 + 19 new SOP tests)

## What was built
- `ragspine/core/spine.py`: new `sop_pages` table (id/title/client_id/category/content/
  status/author/reviewer/base_version/created_at/updated_at) via SCHEMA (CREATE TABLE
  IF NOT EXISTS — no `_ensure_columns` migration needed since it's a brand-new table).
- `ragspine/business/sop.py`: SOP_TEMPLATE (Klijent/Kategorija/Postupak/Alati/Česte
  greške/Izvor) + new_sop_content(); create_sop / submit_draft / approve_draft /
  reject_draft / list_pending / get_sop / editorial_summary / update_draft.
  approve_draft calls `ingest.ingest_text(..., doc_type="sop", title=f"SOP: {title}")`
  so the approved content lands in the RAG corpus and authority.detect_authority
  recognizes the "SOP:"/doc_type=sop title as tier `interna_procedura` (0.7).
  State-machine guards (draft->submitted->approved|rejected) raise ValueError on
  invalid transitions, matching the existing rag/versioning.py convention. Only
  draft/rejected SOPs are editable via update_draft (approved = immutable, bumps
  base_version on edit).
- `ragspine/web/api.py`: POST /sop, POST /sop/{id}/submit, POST /sop/{id}/approve
  (returns doc_id), POST /sop/{id}/reject, GET /sop/pending (items + HR summary
  text), GET /sop/{id} — all on require_user_web. Route order: /sop/pending
  registered before /sop/{sop_id} so it isn't swallowed by the int path param.
- `tests/test_sop.py`: 19 TDD tests — template fill, state machine (create/submit/
  approve/reject + invalid-transition guards, no-ingest-on-guard-failure),
  list_pending filtering, editorial_summary count, get_sop, update_draft version
  bump + draft/rejected-only + approved-refused, authority-tier detection on the
  approved doc, and full API round-trip (create->submit->approve->pending->get,
  plus reject path, plus 401 without auth).

## Concerns / upgrade paths
- Approval is currently open to any authenticated user (per instructions — "do
  not block on role"). Marked with a `ponytail:` comment in web/api.py: upgrade
  path is a role check (only 'voditelj'/admin approves) once users.role is
  enforced elsewhere in the app.
- editorial_summary uses simple Croatian phrasing ("N SOP-a čeka pregled") without
  full number-declension handling (1/2-4/5+ forms) — fine for internal tooling,
  not localization-grade.
- No client-name lookup in new_sop_content's "Klijent" section (template leaves
  "-"; client_id is stored on the row separately) — trivial to wire if needed.

## Fix round (review feedback)

Commit: (see below)
Full suite: `python -m pytest tests/ -q` -> 498 passed, 1 skipped (495 + 3 new tests)

1. IMPORTANT — state-machine dead-end fixed: `submit_draft` now accepts
   `status in ('draft', 'rejected')` (new `SUBMITTABLE_STATUSES`), not just
   `'draft'`. A rejected SOP can be fixed via `update_draft` and resubmitted;
   audit action is `sop_resubmit` when the prior status was `rejected` (vs
   `sop_submit` from a fresh draft). `submit_draft` from `approved` still
   raises `ValueError` (no path backward from approved). New tests:
   `test_resubmit_after_reject_works` (create→submit→reject→update_draft→
   submit_draft→'submitted'→approve succeeds) and
   `test_submit_from_approved_still_refused`.
2. MINOR — idempotency guard verified explicitly: new test
   `test_approve_already_approved_raises_no_double_ingest` asserts
   `approve_draft` on an already-approved SOP raises `ValueError` and the
   `documents` row count is unchanged (no double ingest). Also strengthened
   `test_approve_draft_requires_submitted_status` to assert the `documents`
   count doesn't change when approving a still-`draft` SOP.
3. MINOR — added a `ponytail:` comment in `approve_draft` noting the ingest
   and the status `UPDATE` aren't in one transaction (a failed UPDATE after a
   successful ingest could leave an orphan corpus doc); acceptable
   fail-closed, upgrade path is wrapping both in one `spine.write()` block.
   No restructuring done.
