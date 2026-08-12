"""Phase 3: iterative verified answer. Before answering, ATLAS makes several
passes — retrieve -> draft -> verify citations -> broaden the search if weak ->
again — and only then decides. Below the threshold (80%) it does not answer but
explains why. Capped at MAX_PASSES; without an LLM it is not called (the pipeline
degrades separately)."""
import re

from atlas.rag import budget, citations, composer, conversation, retrieval
from atlas.rag.authority import detect_authority

THRESHOLD = 0.80
MAX_PASSES = 3


def _reformulate(query: str, hits) -> str:
    """Broaden the query with distinguishing words from the titles of the best
    sources (cheap, without an extra LLM call) so the next pass retrieves a
    broader/more precise set."""
    have = set(re.findall(r"\w+", query.lower()))
    extra: list[str] = []
    for h in hits[:3]:
        for w in re.findall(r"\w+", (h.title or "").lower()):
            if len(w) >= 4 and w not in have and w not in extra:
                extra.append(w)
    return (query + " " + " ".join(extra[:4])).strip()


def _merge(a, b):
    seen = {h.chunk_id for h in a}
    return a + [h for h in b if h.chunk_id not in seen]


def run(spine, query: str, hits, llm, prior_turns=None,
        threshold: float = THRESHOLD, max_passes: int = MAX_PASSES, extra: str = "",
        org_id=None, visible_client_ids=None) -> dict:
    """Return the best candidate: {text, report, confidence, cited_hits, hits, passes, threshold}.
    llm.complete may raise LLMError/LLMUnavailable — it is propagated to the caller."""
    best = None
    prev_conf = -1.0
    for p in range(1, max_passes + 1):
        # Compact BEFORE compose and verify against the same (compacted) set —
        # the model must not earn a point for citing a source it did not see,
        # nor for a fact cut off by truncation.
        hits = budget.compact(hits)
        system, messages = composer.compose(query, hits, extra=extra)
        if prior_turns:
            messages = conversation.as_messages(prior_turns) + messages
        result = llm.complete(messages, system=system)
        report = citations.verify(result.text, hits)
        cited_hits = [hits[n - 1] for n in report.cited if 1 <= n <= len(hits)]
        # Grounded = at least one source was actually cited. An empty retrieval
        # makes citations.verify return ok=1.0 (nothing to check) — for a legal
        # assistant that is a yes-man hole, so without a cited source confidence = 0.
        grounded = report.ok and bool(cited_hits)
        conf = citations.blend_authority(report.confidence, cited_hits) if grounded else 0.0
        cand = {"text": result.text, "report": report, "confidence": conf,
                "cited_hits": cited_hits, "hits": hits, "passes": p}
        if best is None or conf > best["confidence"]:
            best = cand
        if conf >= threshold:
            break
        if conf <= prev_conf:  # no progress — do not spend further passes
            break
        if p >= max_passes or not hits:  # last pass / empty retrieval — no broadening
            break
        prev_conf = conf
        # org_id MUST follow the expansion too — without it the 2nd+ pass would
        # escape the tenant filter and mix other tenants' documents into the context
        more = retrieval.search(spine, _reformulate(query, hits), k=len(hits) + 4, org_id=org_id,
                                visible_client_ids=visible_client_ids)
        merged = _merge(hits, more)
        if len(merged) == len(hits):  # nothing new — no point repeating the same draft
            break
        hits = merged
    best["threshold"] = threshold
    return best


def grounded(best: dict) -> bool:
    return best["report"].ok and bool(best["cited_hits"])


def accepted(best: dict) -> bool:
    return grounded(best) and best["confidence"] >= best["threshold"]


def reason(best: dict) -> str:
    if not grounded(best):
        return "nema pokrivajućeg izvora u bazi"
    return "izvori su preslabi ili nepotpuni za pouzdan odgovor"


def explain(best: dict) -> str:
    hits = best["cited_hits"]
    n = len(hits)
    if not hits:
        return f"temeljeno na {n} izvora"
    top = max((detect_authority(h.title, doc_type=h.doc_type) for h in hits), key=lambda t: t[1])
    return f"temeljeno na {n} izvor(a); najviši autoritet: {top[0]}"
