# T1 — knjiženje lane (report)

## Status: DONE

## Deliverables
- `ragspine/core/spine.py`: `konto_corrections` table added to SCHEMA (CREATE TABLE IF NOT EXISTS).
- `ragspine/business/kategorizacija.py`: 15 diacritic-insensitive regex RULES + `categorize()`.
- `ragspine/business/feedback_learn.py`: `_norm`, `record_correction()` (INSERT + audit), `suggest_from_feedback()`
  (stem-overlap match over prior corrections, most-frequent corrected_konto wins, confidence
  `min(0.95, 0.7 + count*0.05)`).
- `ragspine/business/knjizenje.py`: `suggest()` (naučeno > pravilo > kontni-plan > fallback priority)
  + `handle()` lane handler, registered into `pipeline.LANE_HANDLERS["knjizenje"]` (lazy import, mirrors sql_lane.py).
- `ragspine/web/api.py`: `POST /knjizenje` and `POST /knjizenje/correct` (both `require_user_web`), plus the
  import that registers the lane handler at serve time.
- `tests/test_knjizenje.py`: 11 new tests (rule categorization, diacritics, learning beats rule, rising
  confidence, kontni_plan naziv enrichment, lane-handler wiring via pipeline.answer, both API endpoints).

## Design notes
- Croatian declension (`restoranu` vs `restoran`) breaks exact-word overlap, so both the feedback-learning
  match and the kontni_plan keyword search use a 5-char prefix stem (`ponytail:` comment in
  feedback_learn.py — upgrade path is a real stemmer if prefix collisions become a problem).
- `konto` numbers/names in kategorizacija.RULES are illustrative (RRIF-style skupina-4 layout), not an
  official chart — flagged with a `ponytail:` header comment; real accounts come from the `kontni_plan`
  table or learned corrections.
- SQL is fully parametrized (`?` placeholders); the only f-string builds a repeated `OR` clause count, never
  interpolates user data.

## Test command
`python -m pytest tests/ -q`

## Result
316 passed, 1 skipped (was 305 baseline; +11 new, 0 broken).

## Concerns
- Konto numbers in RULES are placeholders, not a verified official chart-of-accounts — operator should
  reconcile against their real `kontni_plan` seed data (matches existing quickref.py-style ponytail caveat).
- Stem-prefix matching (5 chars) is a heuristic, not real Croatian stemming; short/ambiguous descriptions
  could theoretically collide on shared prefixes across unrelated words — acceptable given the small
  expected vocabulary of expense descriptions, but worth a real diagnostic look if false-positive learned
  suggestions show up in practice.
