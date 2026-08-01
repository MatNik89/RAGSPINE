from ragspine.business import notes, sop
from ragspine.rag import client_context, pipeline
from ragspine.docs import ingest as ing
from ragspine.core.llm import LLMClient


def _client(spine, name, oib):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name, oib) VALUES(?,?)", (name, oib)).lastrowid


def _approved_sop(spine, title, category, client_id=None):
    sop_id = sop.create_sop(spine, "ana", title, category, f"Postupak: {title}.", client_id=client_id)
    sop.submit_draft(spine, sop_id, "ana")
    sop.approve_draft(spine, sop_id, "iva")
    return sop_id


def _llm(cfg, text):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    return LLMClient(cfg, transport=lambda u, h, b: {
        "choices": [{"message": {"content": text}}], "model": "m", "usage": {}})


def test_resolve_client_found(spine):
    cid = _client(spine, "Pekara Mlinar", "111")
    result = client_context.resolve_client(spine, "kako se radi plaća za Pekaru Mlinar")
    assert result is not None
    assert result["id"] == cid
    assert result["name"] == "Pekara Mlinar"


def test_resolve_client_none_for_unrelated_query(spine):
    _client(spine, "Pekara Mlinar", "111")
    assert client_context.resolve_client(spine, "koliki je pdv?") is None


def test_client_sops_filters_by_topic(spine):
    cid = _client(spine, "Pekara Mlinar", "111")
    sop_id = _approved_sop(spine, "Plaća za Pekaru — specifičnosti", "place", client_id=cid)
    _approved_sop(spine, "Nešto sasvim drugo", "ostalo", client_id=cid)

    result = client_context.client_sops(spine, cid, "plaća")
    titles = [r["title"] for r in result]
    assert "Plaća za Pekaru — specifičnosti" in titles
    ids = [r["sop_id"] for r in result]
    assert sop_id in ids


def test_client_sops_only_this_client_and_approved(spine):
    a = _client(spine, "Klijent A", "1")
    b = _client(spine, "Klijent B", "2")
    _approved_sop(spine, "Plaća za B", "place", client_id=b)
    draft_id = sop.create_sop(spine, "ana", "Plaća za A draft", "place", "x", client_id=a)

    result = client_context.client_sops(spine, a, "plaća")
    ids = [r["sop_id"] for r in result]
    assert draft_id not in ids  # not approved
    result_b = client_context.client_sops(spine, b, "plaća")
    titles_b = [r["title"] for r in result_b]
    assert "Plaća za B" in titles_b


def test_client_notes_recent_newest_first(spine):
    cid = _client(spine, "Pekara Mlinar", "111")
    notes.add(spine, cid, "ana", "Prva bilješka")
    notes.add(spine, cid, "ana", "Druga bilješka")

    result = client_context.client_notes_recent(spine, cid, limit=5)
    assert len(result) == 2
    assert result[0]["body"] == "Druga bilješka"
    assert result[1]["body"] == "Prva bilješka"


def test_client_note_block_with_sop_and_note(spine):
    cid = _client(spine, "Pekara Mlinar", "111")
    _approved_sop(spine, "Plaća za Pekaru — specifičnosti", "place", client_id=cid)
    notes.add(spine, cid, "ana", "Kasni s dostavom dokumentacije")

    block = client_context.client_note_block(spine, cid, "Pekara Mlinar", "plaća")
    assert "Napomena za klijenta Pekara Mlinar" in block
    assert "Plaća za Pekaru — specifičnosti" in block
    assert "Kasni s dostavom dokumentacije" in block


def test_client_note_block_empty_when_nothing_specific(spine):
    cid = _client(spine, "Klijent Bez Ičega", "999")
    block = client_context.client_note_block(spine, cid, "Klijent Bez Ičega", "plaća")
    assert block == ""


def test_pipeline_appends_napomena_for_named_client(spine, cfg):
    ing.ingest_text(spine, "Plaća se obračunava do 15-og u mjesecu.", "place", doc_type="zakon")
    cid = _client(spine, "Pekara Mlinar", "111")
    _approved_sop(spine, "Plaća za Pekaru — specifičnosti", "place", client_id=cid)

    r = pipeline.answer(spine, cfg, "kako se radi plaća za Pekaru Mlinar", "ana",
                         llm=_llm(cfg, "Plaća se obračunava do 15-og [1]."))
    assert "Napomena za klijenta Pekara Mlinar" in r["answer"]
    assert r.get("client") is not None
    assert r["client"]["name"] == "Pekara Mlinar"


def test_pipeline_no_napomena_when_no_client_named(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "pdv", doc_type="zakon")
    r = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana", llm=_llm(cfg, "Stopa je 25% [1]."))
    assert "Napomena za klijenta" not in r["answer"]
    assert not r.get("client")
