"""Zero-config OAuth: when a Claude/OpenAI OAuth token is auto-detected and the
operator has chosen no model, ATLAS defaults a sensible model instead of failing
with a cryptic provider 'model too short' error. No network (transport mocked)."""
from dataclasses import dataclass

from atlas.core import llm


@dataclass
class _Cfg:
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_path: str = ""


def _client(monkeypatch, provider_token, captured):
    monkeypatch.setattr(llm, "load_oauth_token", lambda: provider_token)
    monkeypatch.setattr(llm, "_ollama_alive", lambda cfg: False)

    def _fake_transport(url, headers, body):
        captured["body"] = body
        # dual-shaped so both the Anthropic and OpenAI response parsers are satisfied
        return {"model": body["model"], "content": [{"type": "text", "text": "ok"}],
                "choices": [{"message": {"content": "ok"}}], "usage": {}}

    return llm.LLMClient(_Cfg(), transport=_fake_transport)


def test_anthropic_oauth_defaults_model(monkeypatch):
    cap = {}
    c = _client(monkeypatch, ("anthropic-oauth", "sk-ant-oat-xxx"), cap)
    res = c.complete([{"role": "user", "content": "hi"}])
    assert cap["body"]["model"] == "claude-haiku-4-5-20251001"   # defaulted, not empty
    assert res.text == "ok"


def test_openai_oauth_defaults_model(monkeypatch):
    cap = {}
    c = _client(monkeypatch, ("openai-oauth", "tok"), cap)
    c.complete([{"role": "user", "content": "hi"}])
    assert cap["body"]["model"] == "gpt-5"


def test_explicit_model_wins_over_default(monkeypatch):
    cap = {}
    c = _client(monkeypatch, ("anthropic-oauth", "x"), cap)
    c.cfg.llm_model = "claude-opus-4-8"
    c.complete([{"role": "user", "content": "hi"}])
    assert cap["body"]["model"] == "claude-opus-4-8"             # operator choice respected


def test_non_oauth_empty_model_not_defaulted(monkeypatch):
    """Explicit provider+key but no model = operator error -> not silently defaulted."""
    cap = {}
    monkeypatch.setattr(llm, "_ollama_alive", lambda cfg: False)

    def _fake_transport(url, headers, body):
        cap["body"] = body
        return {"model": body["model"], "content": [{"type": "text", "text": "ok"}], "usage": {}}

    cfg = _Cfg(llm_provider="anthropic", llm_api_key="key")      # explicit, no model
    llm.LLMClient(cfg, transport=_fake_transport).complete([{"role": "user", "content": "hi"}])
    assert cap["body"]["model"] == ""                            # left empty (not OAuth)
