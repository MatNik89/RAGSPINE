"""Agent-loop guards (MateClaw patterns, adapted to ATLAS's simple loop):
(1) StructuredTruncator — cut tool results at a JSON boundary + a fidelity note (so the
model does not "fix"/invent the truncated part); (2) evidence-guard — an OIB in the
answer that NO tool actually saw = unverified (anti-hallucination for accounting);
(3) loop-guard key (same tool+args repeated = no progress)."""
import json
import re

from atlas.core import security

# LLM-facing note appended to truncated tool results (kept Croatian — the assistant
# reasons in Croatian; this is prompt content, like tool descriptions).
_FIDELITY = "\n…[skraćeno — izostavljeni dio NIJE poznat; ne izmišljaj nastavak]"
_OIB_RE = re.compile(r"\b\d{11}\b")
_STRUCT_SEPS = ("},", "],", "}", "]", ",")


def truncate_structured(text: str, limit: int = 6000) -> str:
    """Cut at the last structural boundary (,/}/]) within the limit — the retained part
    does not end mid-token (otherwise the model invents a 'fix'). The fidelity note makes
    clear it is incomplete (the model does NOT parse it as valid JSON). Total <= limit
    (note reserved; Codex). The cut may fall inside a string — the note signals that."""
    if text is None or len(text) <= limit:
        return text or ""
    budget = max(1, limit - len(_FIDELITY))
    cut = text[:budget]
    for sep in _STRUCT_SEPS:
        i = cut.rfind(sep)
        if i > budget * 0.5:
            return cut[:i + len(sep)] + _FIDELITY
    return cut + _FIDELITY


def observed_oibs(text: str) -> set:
    """OIBs seen in the content (for evidence accumulation; cheap, a set not a string)."""
    return set(_OIB_RE.findall(text or ""))


def unverified_oibs(answer: str, observed) -> list[str]:
    """Return VALID OIBs the answer states that are NOT in the observed set (`observed`
    = set of OIB strings from tool results + the query). Catches PURE fabrication (an OIB
    that appears nowhere); does NOT catch mis-attribution (correct OIB, wrong client) —
    that is entity-binding, a larger change. OIB = 11 digits + checksum = high precision."""
    ans = {o for o in _OIB_RE.findall(answer or "") if security.oib_valid(o)}
    if not ans:
        return []
    obs = observed if isinstance(observed, (set, frozenset)) else set(_OIB_RE.findall(observed or ""))
    return sorted(ans - obs)


def loop_key(name, args: dict) -> str:
    """Call signature (tool+args) — the same one repeated = no progress. Tolerates
    name=None (malformed call; Codex)."""
    n = str(name or "")
    try:
        return n + "|" + json.dumps(args or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return n + "|" + str(args)


def append_evidence_caution(text: str, unverified: list[str]) -> str:
    if not unverified:
        return text
    joined = ", ".join(unverified)
    # user-facing caution appended to the answer -> Croatian
    return (text or "") + ("\n\n⚠ Nisam iz podataka potvrdio OIB: " + joined +
                           " — provjerite prije korištenja.")
