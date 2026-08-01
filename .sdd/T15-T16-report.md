# T15 + T16 report

## T15 — auto-detect missing tool (capability gap)

`ragspine/knowledge/features.py`:
- `LOWCONF_PHRASES` — 5 Croatian low-confidence markers.
- `_fold()` — lowercase + strip diacritics (unicodedata NFKD), matching the
  existing pattern used in `ragspine/rag/authority.py` / `nldate.py`.
- `detect_missing_tool(answer, confidence, threshold=0.3) -> bool` — True if
  `confidence < threshold` OR a low-confidence phrase is found in the
  diacritic-folded answer.
- `maybe_file_gap(spine, user, query, answer, confidence) -> int|None` —
  files a `feature_request` (category `capability-gap`, priority 2, body
  `Auto: nisko povjerenje na upit: {query[:150]}`) unless not a gap, or an
  open request with the same query snippet already exists (`LIKE` dedupe).

Wired into `ragspine/rag/pipeline.py`: right after the chat-lane `_record()`
call (the branch that computes `final_text`/`confidence` from citation
verification — IDK or cited answer), wrapped in `try/except Exception: pass`
so a gap-filing failure can never break the answer. Not called on
reject/monthly/no_retrieval/lane-handler paths.

## T16 — patterns.detect Jaccard clustering

`ragspine/knowledge/patterns.py`:
- `STOPWORDS` — small hand-picked Croatian stopword set (ponytail-marked:
  upgrade to a real lemmatizer if false negatives show up on real logs).
- `_keywords(query) -> set` — fold diacritics, tokenize `[a-z0-9]+`, drop
  stopwords and length-1 tokens.
- `_jaccard(a, b) -> float` — `|a∩b|/|a∪b|`, `0.0` if both empty.
- `detect()` — same signature/return shape, but now greedily clusters
  interactions: each query joins the first existing cluster whose
  representative (first member's) keyword set has Jaccard > 0.5, else starts
  a new cluster. Clusters with `count >= min_count` become/update a
  `skill_suggestions` row (`pattern` = alphabetically-first raw query in the
  cluster, `params` = sorted distinct raw queries). Deterministic (no
  randomness), one pass, O(n × clusters).

No stemming/lemmatization was added — deliberately kept to exact
keyword-token matching (diacritic-folded) since that's what the TDD contract
in `tests/test_patterns_jaccard.py` needed; the existing exact-match tests in
`tests/test_knowledge_misc.py` (`test_detect_finds_repeated_pattern`,
`test_detect_below_min_count_no_suggestion`) were re-checked against the new
clustering and needed **no changes** — they still pass unmodified (both
"top N klijenata" variants and the single-row below-threshold case cluster
the same way under Jaccard as they did under exact match).

## Tests (TDD, red → green)

- `tests/test_features_gap.py` — 9 tests: `detect_missing_tool` phrase/low-conf/
  good-answer/diacritic cases, `maybe_file_gap` file/dedupe/no-file, and two
  pipeline-integration tests (IDK auto-files a gap, cited good answer does not).
- `tests/test_patterns_jaccard.py` — 9 tests: `_keywords` stopword+diacritic
  stripping, `_jaccard` identical/disjoint/both-empty/partial, and `detect()`
  clustering (5 differently-worded prirez queries → 1 suggestion, 4 → none,
  two distinct topics → 2 separate suggestions, not merged).

## Status

Status: DONE
Commit: e3651b0
Full-suite summary: 516 passed, 1 skipped (baseline 498 + 18 new; pre-existing skip unrelated), 0 failed.
Concerns:
- `maybe_file_gap` dedupe uses `LIKE '%snippet%'` on `body` — fine at this
  scale (small feature_requests table), but a substring match could in
  theory collide if two different queries share a 150-char-truncated prefix
  containing another's snippet; not a concern for real Croatian queries.
- Jaccard clustering keeps a fixed "representative" per cluster (the first
  member seen) rather than an evolving union — matches the spec's wording
  ("representative keyword set") and keeps behaviour deterministic, but a
  long drifting chain of paraphrases (A~B~C~D where A and D share no
  keywords) would not all land in one cluster. Acceptable at this scale;
  documented as the upgrade path if real query logs show it happening.
- No stemming/lemmatization: inflected Croatian forms (e.g. "Split" vs
  "Splitu") are different keyword tokens and won't cluster on that word
  alone — only exact diacritic-folded tokens count. Ceiling noted in the
  `STOPWORDS` comment; upgrade path is a proper Croatian stemmer if this
  turns out to matter on real traffic.
