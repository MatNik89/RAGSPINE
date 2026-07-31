import pytest
from ragspine.core.llm import LLMClient, detect_provider, LLMUnavailable

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

def test_anthropic_path(cfg):
    cfg.llm_base_url = "https://api.anthropic.com"; cfg.llm_api_key = "k"; cfg.llm_model = "claude-sonnet-5"
    r = LLMClient(cfg, transport=_fake_anthropic).complete(
        [{"role": "user", "content": "hej"}], system="ti si knjigovođa")
    assert r.text == "odgovor"

def test_unavailable(cfg, monkeypatch):
    monkeypatch.setattr("ragspine.core.llm.load_oauth_token", lambda: None)
    monkeypatch.setattr("ragspine.core.llm._ollama_alive", lambda cfg: False)
    with pytest.raises(LLMUnavailable):
        LLMClient(cfg).complete([{"role": "user", "content": "x"}])
