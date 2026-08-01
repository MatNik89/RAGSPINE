from ragspine.business import sop
from ragspine.rag import clarify, pipeline


def _client(spine, name, oib):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name, oib) VALUES(?,?)", (name, oib)).lastrowid


def _approved_sop(spine, title, category, client_id=None):
    sop_id = sop.create_sop(spine, "ana", title, category, f"Postupak: {title}.", client_id=client_id)
    sop.submit_draft(spine, sop_id, "ana")
    sop.approve_draft(spine, sop_id, "iva")
    return sop_id


def test_is_howto():
    assert clarify.is_howto("kako se radi plaća?") is True
    assert clarify.is_howto("koliki je pdv?") is False


def test_mentions_type():
    assert clarify.mentions_type("kako se radi plaća za obrt") == "obrt"
    assert clarify.mentions_type("kako se radi plaća") is None


def test_mentions_client(spine):
    _client(spine, "Pekara Mlinar", "111")
    assert clarify.mentions_client(spine, "kako se radi plaća za Pekara Mlinar") == "Pekara Mlinar"
    assert clarify.mentions_client(spine, "kako se radi plaća") is None


def test_mentions_client_diacritic_insensitive(spine):
    _client(spine, "Krčmarić", "222")
    assert clarify.mentions_client(spine, "placa za Krcmaric d.o.o.") == "Krčmarić"


def test_needs_clarification_triggers_on_multiple_variants(spine):
    a = _client(spine, "Klijent A", "1")
    b = _client(spine, "Klijent B", "2")
    _approved_sop(spine, "Plaća za obrt", "place", client_id=a)
    _approved_sop(spine, "Plaća za poduzeće", "place", client_id=b)

    result = clarify.needs_clarification(spine, "kako se radi plaća?")
    assert result is not None
    assert "obrt" in result["question"]
    assert "poduzece" in result["question"]
    assert len(result["variants"]) == 2


def test_needs_clarification_none_when_type_given(spine):
    a = _client(spine, "Klijent A", "1")
    b = _client(spine, "Klijent B", "2")
    _approved_sop(spine, "Plaća za obrt", "place", client_id=a)
    _approved_sop(spine, "Plaća za poduzeće", "place", client_id=b)
    assert clarify.needs_clarification(spine, "kako se radi plaća za obrt") is None


def test_needs_clarification_none_with_single_variant(spine):
    a = _client(spine, "Klijent A", "1")
    _approved_sop(spine, "Plaća za obrt", "place", client_id=a)
    assert clarify.needs_clarification(spine, "kako se radi plaća?") is None


def test_needs_clarification_none_on_non_howto(spine):
    a = _client(spine, "Klijent A", "1")
    b = _client(spine, "Klijent B", "2")
    _approved_sop(spine, "Plaća za obrt", "place", client_id=a)
    _approved_sop(spine, "Plaća za poduzeće", "place", client_id=b)
    assert clarify.needs_clarification(spine, "koliki je pdv?") is None


def test_needs_clarification_none_when_client_named(spine):
    a = _client(spine, "Klijent A", "1")
    b = _client(spine, "Klijent B", "2")
    _approved_sop(spine, "Plaća za obrt", "place", client_id=a)
    _approved_sop(spine, "Plaća za poduzeće", "place", client_id=b)
    assert clarify.needs_clarification(spine, "kako se radi plaća za Klijent A") is None


def test_pipeline_returns_clarify_lane(spine, cfg):
    a = _client(spine, "Klijent A", "1")
    b = _client(spine, "Klijent B", "2")
    _approved_sop(spine, "Plaća za obrt", "place", client_id=a)
    _approved_sop(spine, "Plaća za poduzeće", "place", client_id=b)

    r = pipeline.answer(spine, cfg, "kako se radi plaća?", "ana")  # no llm — must not be needed
    assert r["lane"] == "clarify"
    assert r["clarify"] is True
    assert r["variants"]
    assert "?" in r["answer"]


def test_pipeline_not_clarify_when_type_specified(spine, cfg):
    a = _client(spine, "Klijent A", "1")
    b = _client(spine, "Klijent B", "2")
    _approved_sop(spine, "Plaća za obrt", "place", client_id=a)
    _approved_sop(spine, "Plaća za poduzeće", "place", client_id=b)

    r = pipeline.answer(spine, cfg, "kako se radi plaća za obrt", "ana")
    assert r["lane"] != "clarify"
