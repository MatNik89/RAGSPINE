# T2 — document generation + numeric hallucination gate (report)

## Status: DONE

## Deliverables
- `ragspine/docs/doc_generator.py`: `DocUnavailable`; `TEMPLATES` dict for `ponuda`/`dopis`/`opomena`
  (each `{"naslov","prose_slots","template"}`); `fill_template()`; `_fmt_money()` (HR comma-decimal,
  dot-thousands, e.g. `"1.234,56 EUR"`); `GateReport` dataclass; `post_render_gate()` — the gate;
  `generate()` (prose-slot isolation + gate run); `generate_from_client()` (spine client lookup, money
  computed in Python, optional LLM prose or plain HR default, non-silent `warning` on gate failure);
  `to_docx()` (optional-guarded via `core.optional.need`).
- `ragspine/web/api.py`: `DocGenerateBody`; `GET /doc/templates`; `POST /doc/generate` (both
  `require_user_web`), `ValueError` (unknown client) mapped to 400.
- `tests/test_doc_generator.py` (15 tests) + `tests/test_doc_api.py` (3 tests): fill/unknown-type,
  unknown-placeholder passthrough, prose-cannot-override-computed-slot, gate pass/fail on real
  `generate()`, gate HR-format parse + mismatch + plain-format, `generate_from_client` for ponuda
  (multi-stavka sum) and opomena, missing-client `ValueError`, gate-failure `warning` propagation
  (via monkeypatched broken template), `to_docx` unavailable/writes-file, API auth + happy path.

## The gate
`post_render_gate(rendered_text, expected_numbers, tol=0.02)`: one regex
(`\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2}`) scans the whole rendered text for every
currency-shaped number in HR (`1.234,56`) or plain (`1234.56`/`1234,56`) form, parses each to
float, and for every Python-computed expected number checks some found number matches within
±0.02. Any expected number not found → `missing`, `ok=False`. `generate()` collects expected
numbers automatically from the per-template money slots (`ponuda`→`ukupno` + each stavka amount
via an internal `_stavke_iznosi` list; `opomena`→`iznos_duga`); `_slot_number()` handles both raw
numerics and already-`_fmt_money`-formatted strings by extracting the numeric substring first
(a plain `float()` on `"250,00 EUR"` fails on the `" EUR"` suffix — caught and fixed during TDD).

## Design notes
- Prose isolation: `generate()` only copies keys from the `prose` dict that are in
  `TEMPLATES[doc_type]["prose_slots"]`; anything else (e.g. an LLM trying to set `"ukupno"`) is
  silently dropped, so the computed value from `values` always wins. Verified by
  `test_prose_cannot_fill_computed_slot`.
- `generate_from_client` never ships a doc that failed the gate silently — it still returns
  `text`+`gate`, but adds `result["warning"] = "numeric gate FAILED: brojke nedostaju u
  dokumentu: [...]"` so the caller (API/UI) must surface it.
- PDF export explicitly skipped (ponytail note in the spec) — `pdfforms.fill()` already covers
  AcroForm PDFs; a plain-text `ponuda`/`dopis`/`opomena` has no form fields to target, so a real
  PDF renderer would be new machinery for a feature not requested beyond DOCX.
- `to_docx` is minimal: one `doc.add_paragraph()` per line, `optional.need("docx", ...)` guard
  raising `DocUnavailable` — matches the `pdfforms.FormUnavailable` pattern exactly.
- SQL is parametrized (`?` placeholder on `client_id`).

## Test command
`python -m pytest tests/ -q`

## Result
338 passed, 1 skipped (was 320 baseline; +18 new, 0 broken). The 1 skip is pre-existing/unrelated
(python-docx is actually installed here, so both `to_docx` tests ran and passed).

## Concerns
- HR number regex requires exactly 2 decimal digits (money-shaped) — a bare integer amount like
  `"250 EUR"` (no decimals) won't be picked up as a "found" number by the gate. Not a problem for
  this feature since `_fmt_money` always emits 2 decimals, but any future caller feeding
  hand-written prose with undecorated integers into the gate should format amounts through
  `_fmt_money` first, not raw `str(int)`.
- `to_docx` is a straight line-per-paragraph dump — no styling/letterhead. Fine for the "minimal"
  ask; upgrade path is a real DOCX template if letterhead/branding is ever required.

## Fix round (review: 2 Important + 1 Minor)

### Important — malformed `stavke` item → unhandled 500
`generate_from_client` indexed `s["iznos"]`/`s["naziv"]` directly; a quote line missing either
key raised a bare `KeyError`, which the `/doc/generate` endpoint didn't catch (only `ValueError`
was mapped to 400). Added `_stavka_iznos(stavka)`: validates `isinstance(dict)` + both keys
present + `iznos` is `Decimal`-convertible, raising `ValueError("neispravna stavka")` on any bad
shape. `generate_from_client` now builds `iznosi_dec` through this validator before anything else
touches the item, so a bad line fails fast and the existing endpoint `except ValueError → 400`
handles it with zero endpoint changes.

### Important — gate regex tore numbers out of dates / bare thousands-integers
`_NUMBER_RE`'s plain alternative `\d+\.\d{2}` matched greedily anywhere in the text, so
`"01.08.2026."` yielded a spurious `1.08` and a bare `"1.234"` (no cents) yielded a spurious
`1.23` — either could produce a **false pass** if an expected computed number happened to be
near one of these accidental values (the scariest kind of bug for a hallucination gate: a false
"everything's fine"). Fixed by wrapping the whole alternation in boundary assertions:
`(?<!\d)(?:...)(?!\d)(?!\.\d)` — a match is now rejected if it's immediately preceded by a digit,
immediately followed by a digit, or immediately followed by `.digit` (which is what tears the
first two segments off a dotted date or a longer dotted number). Real currency matches
(`"1.234,56 EUR"`, `"250,00 EUR"`, `"1234.56 EUR"`) are bordered by spaces/punctuation-non-digit
and are unaffected.

### Minor — float sum for `ukupno` → Decimal
`ukupno` was `sum(float, ...)`; switched to `sum(Decimal, Decimal("0")).quantize(Decimal("0.01"))`
so the computed expected total is exact instead of float-sum-drift, while individual stavka
values are also parsed once via `Decimal(str(iznos))` (also closes the validation gap above).
Gate `tol=0.02` unchanged and still absorbs any real-world rounding.

### Also
One-line `ponytail:` comment added above the `llm is not None` branch in
`generate_from_client` noting that `/doc/generate` never passes an `llm`, so real LLM-authored
prose wiring is deferred until the route grows an explicit opt-in — default HR prose is used for
now (as originally shipped).

### New tests (+3)
- `tests/test_doc_generator.py::test_post_render_gate_does_not_tear_number_from_date` —
  `post_render_gate("Datum 01.08.2026. Ukupno 250,00 EUR", [1.08])` → `ok is False`
  (1.08 correctly absent, not fabricated from the date); `[250.00]` → `ok is True`.
- `tests/test_doc_generator.py::test_generate_from_client_malformed_stavka_raises` —
  `stavke=[{"naziv": "X"}]` (no `iznos`) → `ValueError`, not `KeyError`.
- `tests/test_doc_api.py::test_doc_generate_malformed_stavka_returns_400` — same malformed
  stavka through `POST /doc/generate` → `400`, not `500`.

### Test command
`python -m pytest tests/test_doc_generator.py tests/test_doc_api.py -q` then
`python -m pytest tests/ -q`

### Result
`tests/test_doc_generator.py` + `tests/test_doc_api.py`: 21 passed. Full suite: 341 passed,
1 skipped (was 338; +3 new, 0 broken).
