# T4 — source authority weighting + inline legal-citation extraction

## Status
DONE.

## Commit
89e9d3b — feat: source authority weighting + inline legal-citation extraction

## Test summary
`python -m pytest tests/ -q` → 373 passed, 1 skipped (pre-existing, unrelated to T4). Baseline was 357; +16 new tests in tests/test_authority.py.

## What was built
- `ragspine/rag/authority.py`:
  - `AUTHORITY` — 8-tier weight table (zakon 1.0 down to interna_procedura 0.7, default 0.5).
  - `detect_authority(title, path, doc_type)` — diacritic-folded (`unicodedata` NFKD) keyword match against Croatian source patterns, ordered per spec (zakon → pravilnik → uredba → kolektivni_ugovor → mišljenje porezna → nn_objava → interna_procedura → strukovno → default).
  - `authority_bonus(hits)` — max tier weight across hit titles/doc_types; 0.5 when no hits.
  - `blend_authority(base_confidence, hits)` — `base*0.7 + bonus*0.3`, clamped [0,1].
  - `extract_references(text)` — regex-mines članak/čl., bare Zakon/Pravilnik, and NN N/YYYY references, deduped. Law-name capture is a single token (`[^\s.,;()\n]+`, one negated-char-class `+`, no nested quantifiers → linear time, no ReDoS) rather than a multi-word phrase; noted as a `ponytail:` comment with the upgrade trigger, since every reference in this domain ("Zakon o PDV-u", "Pravilniku o PDV-u") is single-token and multi-word law names aren't in scope/tests.
  - `index_references(spine, doc_id, text)` — upserts a `kg_nodes(kind="doc", value=str(doc_id))` node plus one node per extracted reference, links them with `kg_edges(rel="cites")`. Deliberately does *not* import graphrag's `_node_id` — doing so would create `authority → graphrag → pipeline → citations → authority`, a real import cycle, so it carries its own 4-line duplicate instead.
  - `related_documents(spine, hits, limit=5)` — walks hit-docs' `cites` edges to shared reference nodes, then back out to sibling doc nodes, returns `{"title","doc_id"}`.
- `ragspine/rag/citations.py`: re-exports `blend_authority` from authority.py (single source of truth, no duplicated blending logic).
- `ragspine/rag/pipeline.py`: chat lane now does `confidence = citations.blend_authority(report.confidence, hits)` after `citations.verify`, and (only on the `report.ok` branch, wrapped in try/except so it's best-effort) appends `"\n\n📎 Povezani dokumenti: <title>, ..."` from `authority.related_documents`. The no-citation IDK branch is untouched — authority never fires there.
- `ragspine/docs/ingest.py`: `ingest_text` gained a third lazy-import hook (`from ragspine.rag import authority; authority.index_references(...)`), mirroring the existing embed/graphrag `try/except ImportError` pattern, right after the graphrag hook.

## Verified behavior
- `detect_authority` tier/weight pairs match spec exactly, including the diacritic-insensitive "misljenje porezne uprave" variant.
- `authority_bonus`: Zakon-titled hit → 1.0, SOP-titled hit → 0.7, no hits → 0.5.
- `extract_references` on "Prema članku 85. Zakona o PDV-u i Pravilniku o PDV-u (NN 79/2023)..." yields exactly clanak(article=85)/pravilnik/nn(79/2023); the clanak match's span is tracked and the generic bare-Zakon regex skips overlapping spans so the same "Zakona o PDV-u" text doesn't also produce a redundant standalone `zakon` reference. Repeated identical mentions dedup to one entry.
- `blend_authority(0.6, zakon_hit)` = 0.72, `blend_authority(0.6, sop_hit)` = 0.63 — Zakon-grounded answers score higher at equal citation coverage.
- `index_references` + `related_documents`: two docs both citing "Zakon o PDV-u" (verbatim shared reference) surface each other; verified via the real `ingest_text` hook, not a direct call.
- Pipeline: two full `pipeline.answer` runs with identical query/LLM-answer but Zakon- vs SOP-titled source docs produce `r_zakon["confidence"] > r_sop["confidence"]`. A citation-free LLM answer still hits the unchanged IDK gate (`confidence == 0`, `"ne znam"` in answer) regardless of the cited doc's authority.

## Concerns / deliberately simplified
- `extract_references`'s law-name capture is single-token by design (see `ponytail:` comment in authority.py) — a real multi-word law name like "Zakon o porezu na dohodak" would only capture "porezu" as the reference value. Not exercised by any spec test or current domain usage (all example/real references in this codebase are single-token, e.g. "PDV-u"); upgrade path is a bounded `{0,N}`-word capture if multi-word names become necessary.
- `related_documents` does 4 sequential small SQL round-trips (own doc nodes → cited refs → sibling doc nodes → titles) rather than one join query — chosen for readability/testability over a single complex JOIN; dataset sizes here (per-answer hit set, not full corpus) make this a non-issue perf-wise.
- Confidence-ordering pipeline test uses two separate `Spine` instances (via `tmp_path`) rather than one shared spine, since each variant needs its own single-document corpus to keep the retrieved hit deterministic.

## Fix round (coordinator review, 1 Important + 1 Minor)

### Status
DONE.

### Commit
(see `git log` — appended after this report was first written)

### Test summary
`python -m pytest tests/test_authority.py -q` → 18 passed. Full suite: `python -m pytest tests/ -q` → 375 passed, 1 skipped (pre-existing, unrelated). +2 new tests on top of the 373 baseline from the first round.

### Fix 1 (Important) — confidence used the full hit list, not the cited subset (`ragspine/rag/pipeline.py`)
`blend_authority(report.confidence, hits)` passed *every* retrieved hit, so a high-authority Zakon sitting in context but never cited by the LLM still inflated confidence — defeating the "answer grounded in a stronger source scores higher" goal. Fixed to build `cited_hits = [hits[n-1] for n in report.cited if 1 <= n <= len(hits)]` from `report.cited` (the verified 1-indexed `[n]` positions) and blend on that subset instead.
Test added: `test_pipeline_confidence_uses_only_cited_hit` — a Zakon and an SOP both sit in context (retrieval.search monkeypatched to return both, order fixed); when the LLM cites `[2]` (the SOP), confidence blends on the SOP's 0.7 bonus; when it cites `[1]` (the Zakon), confidence blends on 1.0. Asserts the exact blended values and that the ordering flips depending on which hit was actually cited.

### Fix 2 (Minor) — tier-selection order let a lower-weight tier win on multi-keyword titles (`ragspine/rag/authority.py`)
`detect_authority`'s elif chain checked `interna_procedura` (0.7) before `strukovno` (0.75), so a title matching both keyword sets (e.g. "Interna procedura Hrvatske komore") landed in the lower tier. Reordered so `strukovno` is checked first. While fixing this, also caught a real matching bug the reorder exposed: the `strukovno` keyword was the nominative `"komora"`, which doesn't match the genitive `"Hrvatske komore"` actually used in the test title (Croatian declension) — broadened to the prefix `"komor"`, consistent with how `"strukovn"` is already prefix-matched for the same reason.
Test added: `test_detect_strukovno_beats_interna_on_multi_match` — `"Interna procedura Hrvatske komore"` → `("strukovno", 0.75)`.

### Unchanged (verified still passing)
The no-citation IDK gate is untouched — `test_pipeline_idk_gate_unaffected_by_authority` still passes unmodified; authority only ever adjusts confidence on an already-cited answer.
