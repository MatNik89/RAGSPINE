# T3 — outbound client messaging gateway

## Status
DONE.

## Commit
1bd6de8 — feat: outbound client messaging gateway with consent gate + audience filters

## Test summary
`python -m pytest tests/ -q` → 352 passed, 1 skipped (pre-existing skip, unrelated to T3). Baseline was 341; +11 new tests in tests/test_messaging.py.

## What was built
- `ragspine/core/spine.py`: added `messaging_consent`, `messaging_channel`, `messaging_target` columns to the `clients` CREATE TABLE, and a new `message_log` table.
- `ragspine/web/messaging.py`:
  - `render_message(subject, body)` — trivial join, extensibility hook.
  - `_log(...)` — inserts message_log with `security.redact_pii(body[:120])` as body_preview.
  - `send_to_client(spine, cfg, client_id, subject, body, dry_run=True)` — consent gate first (no consent or empty target → `skipped_no_consent`, logged, nothing touches apprise); then dry_run short-circuit (`dry_run` status, logged, apprise never imported/called); only when `dry_run=False` and consent+target present does it call `optional.need("apprise", ...)`, build an `Apprise()` client, add the client's target, and `notify()`. Exceptions caught and logged as `type(e).__name__` only (mirrors `ops/digest.deliver`, no credential leakage).
  - `build_audience(spine, filt, **kw)` — `compliance_missing` (kind+period, joins obligations/obligation_status, `COALESCE(sent,0)=0`), `expiring_soon` (kw.days, delegates to `business.expiry.expiring`, dedups client_id), `all_active` (active=1 clients). Unknown filter raises `ValueError`.
  - `send_to_filter(...)` — builds audience, calls `send_to_client` per client respecting consent+dry_run, aggregates `results` as a `{status: count}` dict (spec's bullet text "aggregate counts by status" governed over the bracket notation in the type sketch).
- `ragspine/web/api.py`: `POST /messaging/send`, `POST /messaging/campaign` (400 on bad/missing filter kwargs via KeyError/ValueError), `GET /messaging/log?client_id=`, `POST /clients/{id}/messaging` (validates consent ∈ {0,1}, 404 on unknown client) — all behind `require_user_web`.

## Safety invariants verified by tests
- Consent gate: `messaging_consent=0` or empty `messaging_target` → `skipped_no_consent`, logged, nothing transmitted (2 tests: no consent, no target).
- Dry-run default: consented+targeted client, `dry_run` omitted (defaults True) → status `dry_run`, logged, and `optional.need` is monkeypatched to assert it is **never called** in this path.
- Campaign audience correctness: `compliance_missing` includes only unsent-obligation clients (excludes an already-sent client); results split correctly between `dry_run` (consented) and `skipped_no_consent` (unconsented) counts.
- `expiring_soon`: a 20-day-out expiry item is in the `days=30` audience, not in `days=10`.
- PII redaction: message_log.body_preview redacts a valid OIB to `[OIB]` and is ≤120 chars.
- Consent-management endpoint: `POST /clients/{id}/messaging` flips a client from skipped to dry_run-eligible.
- API campaign smoke test: authenticated `POST /messaging/campaign` with `dry_run: true` → 200, correct `audience` count.

## Concerns / deliberately untested
- Real transmission (`dry_run=False` + apprise installed + `notify()` actually reaching a live service) is intentionally **not** exercised — per the task's own instruction, that branch stays guarded/untested rather than hitting a live service or fabricating an apprise fake that could mask real integration bugs. The dry-run and no-apprise-called assertion is the safety-relevant test; the transmit branch is a straight mirror of `ops/digest.deliver`'s already-tested pattern.
- `send_to_filter`'s `results` field is a `{status: count}` dict, not a list — the task text was self-contradictory here (curly braces + "aggregate counts by status" vs. a bracket in the type sketch); dict was chosen as the more directly useful and testable shape.

## Fix round (coordinator review, 2 IMPORTANT findings)

### Status
DONE.

### Commit
(see below — appended after this report was written, check `git log` for the actual hash of "fix: redact-before-truncate + clients-column migration")

### Test summary
`python -m pytest tests/ -q` → 357 passed, 1 skipped (pre-existing, unrelated). +5 new tests (1 in test_messaging.py, 4 in test_spine.py) on top of the 352 baseline from the first round.

### Fix 1 — redact-before-truncate ordering bug (`ragspine/web/messaging.py` `_log`)
`security.redact_pii(body[:120])` truncated first, so an 11-digit OIB (or IBAN/email/phone) straddling the char-120 cut got split and the regex's `\b\d{11}\b` no longer matched the fragment — raw PII digits leaked into `body_preview`. Fixed to `security.redact_pii(body)[:120]` (redact the full string, truncate after). Also applied the same `redact_pii()` call to `subject` before logging (was stored raw — minor, subjects are office-authored but can be templated with client data).
Test added: `test_message_log_body_preview_redacts_pii_straddling_truncation_cut` (test_messaging.py) — places a valid OIB spanning the old cut point (chars 113–124), asserts no raw OIB or digit-fragment survives in `body_preview` and length stays ≤120.

### Fix 2 — missing ALTER-TABLE migration for `clients` messaging columns (`ragspine/core/spine.py`)
The messaging columns were only added to the `CREATE TABLE IF NOT EXISTS clients(...)` statement, which is a no-op against any already-existing DB — on a real deployed database the columns would never appear and `send_to_client` would raise on the missing-key row access. Added a generic, idempotent `_ensure_columns(conn, table, {col: coldef})` helper that reads `PRAGMA table_info(table)` and issues `ALTER TABLE ... ADD COLUMN` for anything missing; called from `Spine.__init__` right after `executescript(SCHEMA)` for the three messaging columns on `clients`. Reusable for future schema additions.
Tests added (test_spine.py):
- `test_ensure_columns_adds_missing_column_with_default` — helper adds a column to a bare table, existing row backfills to the column default.
- `test_ensure_columns_idempotent_noop_when_present` — calling it again when the column already exists doesn't raise (no duplicate-column error) and doesn't duplicate the column.
- `test_clients_messaging_columns_present` — fresh Spine has all three columns (regression guard on the normal path).
- `test_migration_adds_messaging_columns_to_preexisting_db` — the scenario the bug was about: builds a raw sqlite3 `clients` table *without* the messaging columns (simulating a pre-existing deployed DB), then opens it with `Spine(...)` and confirms the columns are added and an existing row backfills `messaging_consent=0`.

### Unchanged (verified still passing)
Consent gate, dry-run default (apprise never called), and credential-safe exception logging (`type(e).__name__` only) were not touched — all their original tests still pass unmodified.
