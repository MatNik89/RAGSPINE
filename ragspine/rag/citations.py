"""Citation parsing + grounding gate: reject answers that don't cite real sources."""
import re
from dataclasses import dataclass

IDK = "Ne znam — nemam izvor za ovo u bazi."

_REF_RE = re.compile(r"\[(\d+)\]")


@dataclass
class Report:
    cited: list[int]
    confidence: float
    ok: bool


def verify(answer: str, hits) -> Report:
    n = len(hits)
    refs = [int(m) for m in _REF_RE.findall(answer)]
    valid = sorted({r for r in refs if 1 <= r <= n})

    if n == 0:
        return Report(cited=valid, confidence=1.0, ok=True)

    if not refs:
        return Report(cited=[], confidence=0.0, ok=False)

    validity = len(valid) / len(set(refs))
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", answer.strip()) if s]
    cited_sentences = sum(1 for s in sentences if _REF_RE.search(s))
    coverage = cited_sentences / len(sentences) if sentences else 0.0
    confidence = coverage * validity

    return Report(cited=valid, confidence=confidence, ok=bool(valid))
