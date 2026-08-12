"""Context compaction (TIER 2): calibrated token estimator + tiered
score substitution over hits before assembling the prompt.

No tiktoken dependency: Croatian text on common BPE vocabularies runs at
~3 characters per token (diacritics + morphology make it more expensive than
English's ~4). The estimator is deliberately conservative -- better to slightly
underestimate the budget than to overrun the model's context.

Tiers (hits are already score-sorted):
  1. full text while it fits within FULL_SHARE of the budget,
  2. score substitution -- a shortened chunk intro instead of the whole thing while the remainder holds,
  3. the tail is dropped (the model must not cite what it never saw anyway).
The prefix order is preserved, so [n] citations stay stable.
"""
import dataclasses

CHARS_PER_TOKEN = 3.0  # calibrated for hr; ponytail: a per-model map if needed
DEFAULT_BUDGET_TOKENS = 3000
_FULL_SHARE = 0.6      # share of the budget for tier-1 (full chunks)
_TRUNC_CHARS = 400     # score substitution: the intro part of a chunk


def est_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


def _head(text: str, limit: int = _TRUNC_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > 0 else limit].rstrip() + " …"


def compact(hits: list, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> list:
    """Return the (possibly shortened) prefix of hits that fits within the budget."""
    out, used = [], 0
    full_cap = int(budget_tokens * _FULL_SHARE)
    for h in hits:
        cost = est_tokens(h.text)
        if used + cost <= full_cap:
            out.append(h)
            used += cost
            continue
        short = _head(h.text)
        cost = est_tokens(short)
        if used + cost > budget_tokens:
            break  # tier 3: the tail drops out
        out.append(dataclasses.replace(h, text=short))
        used += cost
    return out
