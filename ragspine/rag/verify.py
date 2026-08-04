"""Faza 3: iterativni provjereni odgovor. Prije odgovora RAGSPINE napravi više
prolaza — retrieve → nacrt → provjeri citate → proširi pretragu ako je slabo →
ponovno — pa tek onda odluči. Ispod praga (80%) ne odgovara nego objasni zašto.
Ograničeno na MAX_PASSES; bez LLM-a se ne poziva (pipeline degradira zasebno)."""
import re

from ragspine.rag import budget, citations, composer, conversation, retrieval
from ragspine.rag.authority import detect_authority

THRESHOLD = 0.80
MAX_PASSES = 3


def _reformulate(query: str, hits) -> str:
    """Proširi upit razlikovnim riječima iz naslova najboljih izvora (jeftino,
    bez dodatnog LLM poziva) da idući prolaz dohvati širi/precizniji skup."""
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
    """Vrati najbolji kandidat: {text, report, confidence, cited_hits, hits, passes, threshold}.
    llm.complete može podići LLMError/LLMUnavailable — pušta se pozivatelju."""
    best = None
    prev_conf = -1.0
    for p in range(1, max_passes + 1):
        # Kompaktiraj PRIJE composea i verificiraj nad istim (kompaktiranim)
        # skupom — model ne smije dobiti bod za citat izvora kojeg nije vidio,
        # ni za činjenicu odrezanu truncationom.
        hits = budget.compact(hits)
        system, messages = composer.compose(query, hits, extra=extra)
        if prior_turns:
            messages = conversation.as_messages(prior_turns) + messages
        result = llm.complete(messages, system=system)
        report = citations.verify(result.text, hits)
        cited_hits = [hits[n - 1] for n in report.cited if 1 <= n <= len(hits)]
        # Grounded = stvarno je citiran barem jedan izvor. Prazan retrieval daje
        # citations.verify ok=1.0 (nema što provjeriti) — za pravni asistent to je
        # yes-man rupa, pa bez citiranog izvora povjerenje = 0.
        grounded = report.ok and bool(cited_hits)
        conf = citations.blend_authority(report.confidence, cited_hits) if grounded else 0.0
        cand = {"text": result.text, "report": report, "confidence": conf,
                "cited_hits": cited_hits, "hits": hits, "passes": p}
        if best is None or conf > best["confidence"]:
            best = cand
        if conf >= threshold:
            break
        if conf <= prev_conf:  # nema napretka — ne troši daljnje prolaze
            break
        if p >= max_passes or not hits:  # zadnji prolaz / prazan retrieval — bez širenja
            break
        prev_conf = conf
        # org_id MORA pratiti i ekspanziju — bez njega bi 2.+ prolaz pobjegao
        # iz tenant filtra i umiješao tuđe dokumente u kontekst
        more = retrieval.search(spine, _reformulate(query, hits), k=len(hits) + 4, org_id=org_id,
                                visible_client_ids=visible_client_ids)
        merged = _merge(hits, more)
        if len(merged) == len(hits):  # ništa novo — nema smisla ponavljati isti nacrt
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
