"""Self-RAG: query-complexity classification + LLM relevance check.

classify() decides retrieval depth (k_for) before search runs; check_relevance()
gates the answer *after* retrieval, so the chat lane can fall back to the web
lane when the retrieved chunks don't actually cover the question.
"""
from atlas.core.llm import LLMError, LLMUnavailable

_MARKERS = (" i ", " te ", " ali ", " pa ", " ili ")


def classify(query: str) -> str:
    if len(query) > 120:
        return "complex"
    if sum(query.count(m) for m in _MARKERS) >= 2:
        return "complex"
    if query.count("?") >= 2:
        return "complex"
    return "simple"


def k_for(query: str) -> int:
    return 16 if classify(query) == "complex" else 8


def check_relevance(llm, query: str, hits: list) -> bool:
    if llm is None:
        return True  # can't judge without an LLM — assume relevant

    lines = [f"- {h.title}: {h.text[:200]}" for h in hits]
    prompt = (
        "Jesu li ovi izvori relevantni za pitanje? Odgovori DA ili NE.\n"
        f"Pitanje: {query}\n" + "\n".join(lines)
    )
    try:
        result = llm.complete([{"role": "user", "content": prompt}])
    except (LLMError, LLMUnavailable):
        return True  # fail-open — don't block the answer on an LLM hiccup

    answer = (result.text or "").strip().upper()
    if answer.startswith("NE"):
        return False
    return True  # DA, or anything unparseable — fail-open
