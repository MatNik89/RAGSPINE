from ragspine.knowledge import features
from ragspine.rag import pipeline
from ragspine.docs import ingest as ing
from ragspine.core.llm import LLMClient


def _llm(cfg, text):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    return LLMClient(cfg, transport=lambda u, h, b: {
        "choices": [{"message": {"content": text}}], "model": "m", "usage": {}})


# --- detect_missing_tool ---

def test_detect_missing_tool_phrase():
    assert features.detect_missing_tool("Ne znam.", 0.9) is True


def test_detect_missing_tool_low_confidence():
    assert features.detect_missing_tool("Stopa je 25%.", 0.1) is True


def test_detect_missing_tool_good_answer_false():
    assert features.detect_missing_tool("Stopa je 25% [1].", 0.8) is False


def test_detect_missing_tool_diacritic_insensitive():
    assert features.detect_missing_tool("NEMAM IZVOR za ovo.", 0.9) is True


# --- maybe_file_gap ---

def test_maybe_file_gap_files_request(spine):
    fid = features.maybe_file_gap(spine, "ana", "koliki je prirez u Puli?", "Ne znam.", 0.9)
    assert fid is not None
    rows = features.list_open(spine)
    assert any(r["id"] == fid and r["category"] == "capability-gap" for r in rows)


def test_maybe_file_gap_dedupes_same_query(spine):
    fid1 = features.maybe_file_gap(spine, "ana", "koliki je prirez u Puli?", "Ne znam.", 0.9)
    fid2 = features.maybe_file_gap(spine, "ana", "koliki je prirez u Puli?", "Ne znam.", 0.9)
    assert fid1 is not None
    assert fid2 is None
    rows = [r for r in features.list_open(spine) if r["category"] == "capability-gap"]
    assert len(rows) == 1


def test_maybe_file_gap_no_file_for_good_answer(spine):
    fid = features.maybe_file_gap(spine, "ana", "koliki je prirez?", "Stopa je 25% [1].", 0.8)
    assert fid is None
    rows = features.list_open(spine)
    assert list(rows) == []


# --- pipeline wiring ---

def test_pipeline_idk_autofiles_gap(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "pdv", doc_type="zakon")
    pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana",
                     llm=_llm(cfg, "Izmišljam bez citata."))
    rows = [r for r in features.list_open(spine) if r["category"] == "capability-gap"]
    assert len(rows) == 1


def test_pipeline_good_answer_no_gap(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "pdv", doc_type="zakon")
    pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana",
                     llm=_llm(cfg, "Stopa je 25% [1]."))
    rows = [r for r in features.list_open(spine) if r["category"] == "capability-gap"]
    assert rows == []
