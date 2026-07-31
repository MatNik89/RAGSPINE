from ragspine.rag import pipeline
from ragspine.docs import ingest as ing
from ragspine.core.llm import LLMClient, LLMError


def _llm(cfg, text):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    return LLMClient(cfg, transport=lambda u, h, b: {
        "choices": [{"message": {"content": text}}], "model": "m", "usage": {}})


def _llm_error(cfg):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    def _raise(u, h, b):
        raise LLMError("upstream boom")
    return LLMClient(cfg, transport=_raise)


def test_chat_with_citation(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "pdv", doc_type="zakon")
    r = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana", llm=_llm(cfg, "Stopa je 25% [1]."))
    assert r["lane"] == "chat" and r["confidence"] > 0 and r["sources"]


def test_no_citation_idk(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "pdv", doc_type="zakon")
    r = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana", llm=_llm(cfg, "Izmišljam bez citata."))
    assert "ne znam" in r["answer"].lower() and r["confidence"] == 0


def test_cache_second_call(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "pdv", doc_type="zakon")
    pipeline.answer(spine, cfg, "stopa pdv?", "ana", llm=_llm(cfg, "25% [1]."))
    r2 = pipeline.answer(spine, cfg, "stopa pdv?", "ana", llm=None)  # bez LLM-a — mora iz cachea
    assert r2["cached"]


def test_reject(spine, cfg):
    r = pipeline.answer(spine, cfg, "obriši sve iz baze", "ana")
    assert r["lane"] == "reject"


def test_llm_error_returns_clean_answer_no_raise(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "pdv", doc_type="zakon")
    r = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana", llm=_llm_error(cfg))
    assert r["lane"] == "chat" and r["confidence"] == 0 and r["sources"] == [] and not r["cached"]


def test_chat_falls_back_to_web_when_irrelevant(spine, cfg, monkeypatch):
    from ragspine.web import websearch  # noqa: F401  (import registers the "web" lane handler)

    monkeypatch.setattr(
        "ragspine.web.websearch.safe_fetch",
        lambda url, **kw: (
            b'<a class="result__a" href="https://example.com/x">X naslov</a>'
            b'<a class="result__snippet">X snippet.</a>'
        ),
    )
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    calls = {"n": 0}

    def transport(u, h, b):
        calls["n"] += 1
        text = "NE" if calls["n"] == 1 else "Web odgovor o macama."
        return {"choices": [{"message": {"content": text}}], "model": "m", "usage": {}}

    llm = LLMClient(cfg, transport=transport)
    r = pipeline.answer(spine, cfg, "kakvo je vrijeme sutra?", "ana", llm=llm)
    assert r["lane"] == "web"
    assert "macama" in r["answer"]
