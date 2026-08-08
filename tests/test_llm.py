import pytest
from atlas.core.llm import LLMClient, detect_provider, LLMUnavailable, LLMError

def test_detect():
    assert detect_provider("https://api.anthropic.com") == "anthropic"
    assert detect_provider("http://127.0.0.1:11434/v1") == "ollama"
    assert detect_provider("https://api.deepseek.com") == "openai"

def _fake_openai(url, headers, body):
    assert url.endswith("/v1/chat/completions")
    return {"choices": [{"message": {"content": "odgovor"}}], "model": body["model"], "usage": {}}

def _fake_anthropic(url, headers, body):
    assert url.endswith("/v1/messages") and "anthropic-version" in headers
    return {"content": [{"type": "text", "text": "odgovor"}], "model": body["model"], "usage": {}}

def test_openai_path(cfg):
    cfg.llm_base_url = "https://api.deepseek.com"; cfg.llm_api_key = "k"; cfg.llm_model = "deepseek-chat"
    r = LLMClient(cfg, transport=_fake_openai).complete([{"role": "user", "content": "hej"}])
    assert r.text == "odgovor"

def test_openai_default_path_unchanged(cfg):
    # B10: bez cfg.llm_path (prazno = default) URL mora ostati identičan starom ponašanju.
    cfg.llm_base_url = "https://api.deepseek.com"; cfg.llm_api_key = "k"; cfg.llm_model = "x"
    seen = {}
    def transport(url, headers, body):
        seen["url"] = url
        return {"choices": [{"message": {"content": "ok"}}], "model": body["model"], "usage": {}}
    LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}])
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"

def test_openai_custom_path_for_gemini(cfg):
    # B10: katalog daje nestandardni put za Gemini; klijent mora graditi točan URL.
    cfg.llm_base_url = "https://generativelanguage.googleapis.com"
    cfg.llm_path = "/v1beta/openai/chat/completions"
    cfg.llm_api_key = "k"; cfg.llm_model = "gemini-x"
    seen = {}
    def transport(url, headers, body):
        seen["url"] = url
        return {"choices": [{"message": {"content": "ok"}}], "model": body["model"], "usage": {}}
    LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}])
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

def test_anthropic_path(cfg):
    cfg.llm_base_url = "https://api.anthropic.com"; cfg.llm_api_key = "k"; cfg.llm_model = "claude-sonnet-5"
    r = LLMClient(cfg, transport=_fake_anthropic).complete(
        [{"role": "user", "content": "hej"}], system="ti si knjigovođa")
    assert r.text == "odgovor"

def test_anthropic_malformed_response(cfg):
    cfg.llm_base_url = "https://api.anthropic.com"; cfg.llm_api_key = "k"; cfg.llm_model = "claude-sonnet-5"
    r = LLMClient(cfg, transport=lambda url, headers, body: {"content": [], "model": body["model"], "usage": {}})
    with pytest.raises(LLMError):
        r.complete([{"role": "user", "content": "hej"}])

def test_openai_malformed_response(cfg):
    cfg.llm_base_url = "https://api.deepseek.com"; cfg.llm_api_key = "k"; cfg.llm_model = "deepseek-chat"
    r = LLMClient(cfg, transport=lambda url, headers, body: {"choices": [], "model": body["model"], "usage": {}})
    with pytest.raises(LLMError):
        r.complete([{"role": "user", "content": "hej"}])

def test_unavailable(cfg, monkeypatch):
    monkeypatch.setattr("atlas.core.llm.load_oauth_token", lambda: None)
    monkeypatch.setattr("atlas.core.llm._ollama_alive", lambda cfg: False)
    with pytest.raises(LLMUnavailable):
        LLMClient(cfg).complete([{"role": "user", "content": "x"}])
