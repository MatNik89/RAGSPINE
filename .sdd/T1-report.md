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

## Fix round (review: 1 Critical + 1 Minor)

### Critical — generic-word false match in `feedback_learn.suggest_from_feedback`
Confirmed live: "racun" (Croatian for invoice/receipt) appears in almost every expense
description, survived as a 5-char "significant" stem, so a single shared "racun" stem was
enough for an unrelated stored correction (e.g. reprezentacija -> 4099) to wrongly override a
completely different query (e.g. "racun za novi laptop"). Fixed both parts in
`ragspine/business/feedback_learn.py`:
- (a) Added `STOPWORDS` (racun, racuni, placanje, trosak, troskovi, kupnja, usluga, usluge, za,
  na, od, i, u, po, novi, nova — diacritic-normalized), stripped inside `_significant_words()`
  before any stem is ever built, on both the query side and every candidate row.
- (b) `suggest_from_feedback()` now only counts a row as matching when the shared-stem overlap
  is `>= 2`, or exactly `1` AND that stem is not itself a stopword (belt-and-suspenders check —
  redundant today since stopwords never survive into a stem, but guards future changes to the
  filtering pipeline).

### Minor — ambiguous fallback source in `knjizenje.suggest`
The tier-4 "nothing matched" fallback (confidence 0.2, generic "Ostali troškovi") reused
`source="pravilo"`, indistinguishable from a real rule hit. Changed to `source="nesigurno"`.

### New tests (`tests/test_knjizenje.py`, +4)
- `test_suggest_from_feedback_ignores_generic_word_overlap` — stored reprezentacija correction
  does NOT hijack "racun za novi laptop" (`suggest_from_feedback` returns `None`).
- `test_suggest_falls_back_to_rule_when_only_generic_overlap` — `knjizenje.suggest` for that
  same query falls through to the rule/kontni-plan tier, not `"naučeno"`.
- `test_suggest_from_feedback_matches_single_distinctive_word` — a genuinely similar query
  ("reprezentacija u kafiću", only one overlapping non-generic word, no "restoran") still
  resolves to the learned konto 4099 — proves the fix didn't over-correct into requiring 2+
  words for every legitimate match.
- `test_unmatched_fallback_uses_nesigurno_source` — tier-4 fallback now reports
  `source == "nesigurno"`.

Existing learning tests (rule-beaten-by-correction, rising confidence, kontni_plan naziv
enrichment) re-verified green with no changes needed.

### Test command
`python -m pytest tests/test_knjizenje.py -q` then `python -m pytest tests/ -q`

### Result
`tests/test_knjizenje.py`: 15 passed. Full suite: 320 passed, 1 skipped (was 316; +4 new, 0 broken).
