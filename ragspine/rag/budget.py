"""Kompakcija konteksta (TIER 2): kalibrirani token-estimator + tiered
score-substitucija nad hitovima prije slaganja prompta.

Bez tiktoken ovisnosti: hrvatski tekst na uobičajenim BPE vokabularima ide
~3 znaka po tokenu (dijakritike + morfologija ga čine skupljim od engleskih
~4). Estimator je namjerno konzervativan — bolje malo podcijeniti budžet
nego prekoračiti kontekst modela.

Tieri (hitovi su već score-sortirani):
  1. puni tekst dok stane u FULL_SHARE budžeta,
  2. score-substitucija — skraćeni uvod chunka umjesto cijelog dok stane u ostatak,
  3. rep se ispušta (model ionako ne smije citirati što nije vidio).
Redoslijed prefiksa se čuva, pa [n] citati ostaju stabilni.
"""
import dataclasses

CHARS_PER_TOKEN = 3.0  # kalibrirano za hr; ponytail: po potrebi per-model mapa
DEFAULT_BUDGET_TOKENS = 3000
_FULL_SHARE = 0.6      # udio budžeta za tier-1 (puni chunkovi)
_TRUNC_CHARS = 400     # score-substitucija: uvodni dio chunka


def est_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


def _head(text: str, limit: int = _TRUNC_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > 0 else limit].rstrip() + " …"


def compact(hits: list, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> list:
    """Vrati (možda skraćeni) prefiks hitova koji stane u budžet."""
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
            break  # tier 3: rep ispada
        out.append(dataclasses.replace(h, text=short))
        used += cost
    return out
