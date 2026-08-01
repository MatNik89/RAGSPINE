from ragspine.core.llm import LLMClient
from ragspine.core.spine import Spine
from ragspine.docs import ingest as ing
from ragspine.rag import authority as auth
from ragspine.rag import pipeline
from ragspine.rag import versioning
from ragspine.rag.retrieval import Hit

ZAKON_HIT = Hit(1, 1, "Zakon o PDV-u", "Stopa je 25%.", 1.0, "zakon")
SOP_HIT = Hit(2, 2, "Interni SOP za JOPPD", "Rok je 15 dana.", 1.0, "sop")


def _llm(cfg, text):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    return LLMClient(cfg, transport=lambda u, h, b: {
        "choices": [{"message": {"content": text}}], "model": "m", "usage": {}})


# --- detect_authority ---

def test_detect_zakon():
    assert auth.detect_authority("Zakon o PDV-u") == ("zakon", 1.0)


def test_detect_pravilnik():
    assert auth.detect_authority("Pravilnik o PDV-u") == ("pravilnik", 0.95)


def test_detect_interna_procedura():
    assert auth.detect_authority("Interni SOP za JOPPD") == ("interna_procedura", 0.7)


def test_detect_misljenje_porezna():
    assert auth.detect_authority("Mišljenje Porezne uprave") == ("misljenje_porezna", 0.85)


def test_detect_misljenje_porezna_diacritic_insensitive():
    assert auth.detect_authority("misljenje porezne uprave") == ("misljenje_porezna", 0.85)


def test_detect_unknown_default():
    assert auth.detect_authority("Nešto sasvim drugo") == ("default", 0.5)


def test_detect_strukovno_beats_interna_on_multi_match():
    # "Interna procedura Hrvatske komore" hits both the strukovno (komora) and
    # interna_procedura (interna/procedura) keyword sets; the higher-weight
    # tier (strukovno, 0.75) must win over the lower one (interna, 0.7).
    assert auth.detect_authority("Interna procedura Hrvatske komore") == ("strukovno", 0.75)


# --- authority_bonus ---

def test_authority_bonus_zakon():
    assert auth.authority_bonus([ZAKON_HIT]) == 1.0


def test_authority_bonus_sop_only():
    assert auth.authority_bonus([SOP_HIT]) == 0.7


def test_authority_bonus_no_hits():
    assert auth.authority_bonus([]) == 0.5


# --- extract_references ---

def test_extract_references():
    text = "Prema članku 85. Zakona o PDV-u i Pravilniku o PDV-u (NN 79/2023) obveznik plaća porez."
    refs = auth.extract_references(text)
    kinds = {r["kind"] for r in refs}
    assert "clanak" in kinds and "pravilnik" in kinds and "nn" in kinds

    clanak = next(r for r in refs if r["kind"] == "clanak")
    assert clanak["article"] == 85
    assert "pdv" in clanak["value"].lower()

    nn = next(r for r in refs if r["kind"] == "nn")
    assert "79/2023" in nn["value"]


def test_extract_references_dedup():
    text = "Zakon o PDV-u kaže to. Zakon o PDV-u kaže i ovo."
    refs = [r for r in auth.extract_references(text) if r["kind"] == "zakon"]
    assert len(refs) == 1


# --- blend_authority ---

def test_blend_authority_zakon_higher_than_sop():
    z = auth.blend_authority(0.6, [ZAKON_HIT])
    s = auth.blend_authority(0.6, [SOP_HIT])
    assert round(z, 2) == 0.72
    assert round(s, 2) == 0.63
    assert z > s


def test_blend_authority_bounded():
    assert 0.0 <= auth.blend_authority(1.0, [ZAKON_HIT]) <= 1.0
    assert 0.0 <= auth.blend_authority(0.0, []) <= 1.0


# --- index_references + related_documents ---

def test_index_and_related_documents(spine):
    doc_a = ing.ingest_text(spine, "Zakon o PDV-u propisuje obveze poreznog obveznika.",
                             "Dokument A", doc_type="zakon")
    doc_b = ing.ingest_text(spine, "Prema Zakonu o PDV-u obveznik podnosi prijavu.",
                             "Dokument B", doc_type="zakon")
    assert doc_a is not None and doc_b is not None

    hits = [Hit(1, doc_a, "Dokument A", "", 1.0, "zakon")]
    related = auth.related_documents(spine, hits)
    assert any(r["doc_id"] == doc_b for r in related)


def test_related_documents_excludes_superseded(spine):
    # T4xT11 cross-feature: supersede() marks doc_b status='superseded' but
    # leaves its 'cites' kg_edges intact — related_documents must still filter
    # it out, mirroring retrieval.py's freshness filter.
    doc_a = ing.ingest_text(spine, "Zakon o PDV-u propisuje obveze poreznog obveznika.",
                             "Dokument A", doc_type="zakon")
    doc_b = ing.ingest_text(spine, "Prema Zakonu o PDV-u obveznik podnosi prijavu.",
                             "Dokument B", doc_type="zakon")
    doc_c = ing.ingest_text(spine, "Zakon o PDV-u nova verzija.",
                             "Dokument C", doc_type="zakon")
    assert doc_a is not None and doc_b is not None and doc_c is not None

    hits = [Hit(1, doc_a, "Dokument A", "", 1.0, "zakon")]
    related_before = auth.related_documents(spine, hits)
    assert any(r["doc_id"] == doc_b for r in related_before)
    assert any(r["doc_id"] == doc_c for r in related_before)

    versioning.set_status(spine, doc_b, "superseded")
    related_after = auth.related_documents(spine, hits)
    assert not any(r["doc_id"] == doc_b for r in related_after)
    assert any(r["doc_id"] == doc_c for r in related_after)


def test_related_documents_excludes_draft(spine):
    doc_a = ing.ingest_text(spine, "Zakon o PDV-u propisuje obveze poreznog obveznika.",
                             "Dokument A", doc_type="zakon")
    doc_b = ing.ingest_text(spine, "Prema Zakonu o PDV-u obveznik podnosi prijavu.",
                             "Dokument B (draft)", doc_type="zakon")
    assert doc_a is not None and doc_b is not None

    versioning.set_status(spine, doc_b, "draft")
    hits = [Hit(1, doc_a, "Dokument A", "", 1.0, "zakon")]
    related = auth.related_documents(spine, hits)
    assert not any(r["doc_id"] == doc_b for r in related)


# --- pipeline integration ---

def test_pipeline_confidence_favors_zakon(tmp_path, cfg):
    spine_zakon = Spine(str(tmp_path / "zakon.db"))
    spine_sop = Spine(str(tmp_path / "sop.db"))
    ing.ingest_text(spine_zakon, "Stopa PDV-a je 25 posto.", "Zakon o PDV-u", doc_type="zakon")
    ing.ingest_text(spine_sop, "Stopa PDV-a je 25 posto.", "Interni SOP za JOPPD", doc_type="sop")

    r_zakon = pipeline.answer(spine_zakon, cfg, "kolika je stopa pdv-a?", "ana",
                               llm=_llm(cfg, "Stopa je 25% [1]."))
    r_sop = pipeline.answer(spine_sop, cfg, "kolika je stopa pdv-a?", "ana",
                             llm=_llm(cfg, "Stopa je 25% [1]."))

    assert r_zakon["confidence"] > r_sop["confidence"]


def test_pipeline_confidence_uses_only_cited_hit(spine, cfg, monkeypatch):
    # Both a Zakon and an SOP are in context; confidence must track WHICH one
    # the LLM actually cited, not the strongest source merely present in hits.
    monkeypatch.setattr(pipeline.retrieval, "search",
                         lambda *a, **kw: [ZAKON_HIT, SOP_HIT])

    r_sop_cited = pipeline.answer(spine, cfg, "koji je rok za joppd?", "ana",
                                   llm=_llm(cfg, "Rok je 15 dana [2]."))
    r_zakon_cited = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana",
                                     llm=_llm(cfg, "Stopa je 25% [1]."))

    assert r_zakon_cited["confidence"] > r_sop_cited["confidence"]
    # exact blend: base confidence (coverage*validity=1.0 here) * 0.7 + bonus*0.3
    assert round(r_sop_cited["confidence"], 2) == round(0.7 * 1.0 + 0.3 * 0.7, 2)
    assert round(r_zakon_cited["confidence"], 2) == round(0.7 * 1.0 + 0.3 * 1.0, 2)


def test_pipeline_idk_gate_unaffected_by_authority(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "Zakon o PDV-u", doc_type="zakon")
    r = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana",
                         llm=_llm(cfg, "Izmišljam bez citata."))
    assert "ne znam" in r["answer"].lower() and r["confidence"] == 0
