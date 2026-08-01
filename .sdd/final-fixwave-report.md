# Final fix wave — v2 whole-branch review (T4×T11 leak + T13 scope + messaging SSRF)

Branch: `build/v2-tiers`

## 1. IMPORTANT — T4×T11 cross-feature leak: related_documents() missing status/stale filter

`ragspine/rag/authority.py::related_documents()` final `SELECT id, title FROM documents WHERE id IN (...)`
had no freshness filter, so a document marked `status='superseded'` (via `versioning.set_status`/`supersede`)
or left as `status='draft'` could still surface in the chat "Povezani dokumenti" footer, because its
`kg_edges` ("cites") rows are never removed on status change.

Fix: added `AND (status IS NULL OR status='active') AND (stale IS NULL OR stale=0)` to that SELECT,
mirroring `retrieval.py`'s freshness clause (status + stale; `valid_until` not applicable here since
`related_documents` doesn't carry a query date context — status+stale alone closes the leak T11 requires).

Confirmed `documents` table already has both `status` (TEXT DEFAULT 'active', via `_ensure_columns` in
`core/spine.py`) and `stale` (INTEGER DEFAULT 0, in the base CREATE TABLE) — no migration needed.

Tests added (`tests/test_authority.py`):
- `test_related_documents_excludes_superseded` — doc B superseded after being related → drops out;
  doc C (still active) stays.
- `test_related_documents_excludes_draft` — a draft doc is excluded from the start.

## 2. HARDENING — scope memory decay off the scheduler namespace

`ops/scheduler.py` persists `lastrun.{job}` under `memory(user='scheduler')`. `core/memory.py`'s
`decay_all` and `forget_cold` operated over ALL rows with no user filter — `forget_cold` is an
unscoped `DELETE`, a latent footgun if ever wired into a job (would delete scheduler bookkeeping).

Fix: added `WHERE user != 'scheduler'` to both `decay_all`'s SELECT and `forget_cold`'s DELETE.

Tests added (`tests/test_memory_decay.py`):
- `test_decay_all_skips_scheduler_namespace` — a `scheduler` row's `hot_score` stays 1.0 after 10 years.
- `test_forget_cold_never_deletes_scheduler_namespace` — scheduler row forced to `hot_score=0.0`
  still survives `forget_cold`, while a normal user's cold row is deleted.

## 3. HARDENING — apprise messaging_target scheme allowlist (SSRF/exfil guard)

`ragspine/web/messaging.py::send_to_client` called `apprise.notify()` on `messaging_target`, an
arbitrary URL any authed user can set via `POST /clients/{id}/messaging`, with no scheme check —
`apprise.notify()` makes its own outbound connection, NOT routed through `cfg.egress_allow`, so a
target like `http://127.0.0.1/x` or `json://internal-host` would let an authed user trigger a real
outbound request on `dry_run=False`.

Fix:
- `ALLOWED_TARGET_SCHEMES = {"mailto","mailtos","tgram","discord","slack","twilio","ntfy","pover","pushover"}`
  (ponytail note in code: operator can extend later).
- `_target_scheme_ok(target)` — parses the scheme before `"://"`, lowercases, membership check;
  fail-closed (no `"://"` or unknown scheme → rejected).
- `send_to_client`: after the consent/target gate, before dry-run/send — disallowed scheme →
  status `"skipped_bad_target"`, logged (no target/URL logged, only client id), never reaches apprise.
- `POST /clients/{id}/messaging` (`ragspine/web/api.py`): non-empty target with a disallowed scheme →
  `HTTPException(400, "nedozvoljen kanal")`. Empty target (disable) still allowed.

Tests added (`tests/test_messaging.py`):
- `test_client_messaging_set_rejects_disallowed_scheme` — `http://127.0.0.1/x` → 400.
- `test_client_messaging_set_allows_mailto` — `mailto://…` → 200.
- `test_send_to_client_bad_target_scheme_skipped` — stored `json://internal-host/hook` target,
  `dry_run=False` → `"skipped_bad_target"`, one log row, no transmission attempted.
- `test_send_to_client_mailto_target_passes_scheme_check` — mailto target still reaches (and stops at)
  the existing dry-run gate → `"dry_run"`.

## Verification

Full suite: `python -m pytest tests/ -q` → **524 passed, 1 skipped** (516 baseline + 8 new tests, 0 regressions).

Diff self-reviewed: 4 production files touched (`ragspine/core/memory.py`, `ragspine/rag/authority.py`,
`ragspine/web/api.py`, `ragspine/web/messaging.py`), each change is the minimal SQL/guard clause described
above — no unrelated refactors, no new dependencies.
