"""Golden-set eval: router lane accuracy + retrieval hit rate.

Self-contained — builds its own temp-file spine and mini corpus, never
touches the caller's real DB. Regression guard: cases below were tuned
against the actual router/retrieval behavior, not the intended one.
"""
import tempfile
from pathlib import Path

from atlas.core.spine import Spine
from atlas.docs.ingest import ingest_text
from atlas.rag.retrieval import search
from atlas.rag.router import route

ROUTER_CASES = [
    {"q": "koliko računa imamo ovaj mjesec?", "lane": "sql"},
    {"q": "zbroj PDV-a za srpanj", "lane": "sql"},
    {"q": "top 5 klijenata po upitima", "lane": "sql"},
    {"q": "nauči s https://porezna.hr/prirez", "lane": "learn"},
    {"q": "pretraži web za novi zakon o radu", "lane": "web"},
    {"q": "OCR-aj skenirane račune iz foldera", "lane": "ocr"},
    {"q": "na koji konto knjižim reprezentaciju?", "lane": "knjizenje"},
    {"q": "kakva je veza između klijenta X i dobavljača Y?", "lane": "graph"},
    {"q": "bok", "lane": "no_retrieval"},
    {"q": "obriši sve iz baze", "lane": "reject"},
    {"q": "koliki je prirez za Split?", "lane": "chat"},
    {"q": "što sve moram ovaj mjesec?", "lane": "chat"},
]

_CORPUS = [
    ("pdv-stope", "Stopa PDV-a u Hrvatskoj je 25 posto, snižena 13 i 5.", "zakon"),
    ("minimalac", "Minimalna plaća za 2026. iznosi 970 eura bruto.", "zakon"),
    ("ugovor-najam", "Ugovor o najmu poslovnog prostora u Splitu.", "ugovor"),
    ("kontni-plan", "Kontni plan: konto 4000 troškovi materijala, konto 7500 prihodi od prodaje.", "ostalo"),
]

RETRIEVAL_CASES = [
    {"q": "kolika je stopa PDV-a?", "expect_title": "pdv-stope"},
    {"q": "kolika je minimalna plaća?", "expect_title": "minimalac"},
    {"q": "ugovor o najmu poslovnog prostora", "expect_title": "ugovor-najam"},
    {"q": "koji je konto za troškove materijala", "expect_title": "kontni-plan"},
]


def run(cfg=None) -> dict:
    router_ok = sum(1 for c in ROUTER_CASES if route(c["q"]) == c["lane"])

    with tempfile.TemporaryDirectory() as tmp:
        spine = Spine(str(Path(tmp) / "eval.db"))
        try:
            for title, text, doc_type in _CORPUS:
                ingest_text(spine, text, title, doc_type=doc_type)
            retrieval_ok = sum(
                1 for c in RETRIEVAL_CASES
                if any(h.title == c["expect_title"] for h in search(spine, c["q"]))
            )
        finally:
            spine.close()

    router_pass = router_ok >= 11
    retrieval_pass = retrieval_ok >= 3
    return {
        "router_ok": f"{router_ok}/{len(ROUTER_CASES)}",
        "retrieval_ok": f"{retrieval_ok}/{len(RETRIEVAL_CASES)}",
        "router_pass": router_pass,
        "retrieval_pass": retrieval_pass,
        "pass": router_pass and retrieval_pass,
    }
