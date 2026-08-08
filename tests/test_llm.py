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


# --- tool-calling (Faza 3, Task 2) -----------------------------------------

_TOOLS = [{"name": "pretrazi", "description": "Pretraži.",
           "schema": {"type": "object", "properties": {"upit": {"type": "string"}},
                      "required": ["upit"]}}]


def test_anthropic_tools_sent_in_provider_format(cfg):
    cfg.llm_base_url = "https://api.anthropic.com"; cfg.llm_api_key = "k"; cfg.llm_model = "claude-sonnet-5"
    seen = {}

    def transport(url, headers, body):
        seen["body"] = body
        return {"content": [{"type": "text", "text": "odgovor"}], "model": body["model"], "usage": {}}

    LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}], tools=_TOOLS)
    assert seen["body"]["tools"] == [
        {"name": "pretrazi", "description": "Pretraži.",
         "input_schema": {"type": "object", "properties": {"upit": {"type": "string"}},
                           "required": ["upit"]}}
    ]


def test_anthropic_tool_use_parsed(cfg):
    cfg.llm_base_url = "https://api.anthropic.com"; cfg.llm_api_key = "k"; cfg.llm_model = "claude-sonnet-5"

    def transport(url, headers, body):
        return {"content": [
            {"type": "text", "text": "gledam"},
            {"type": "tool_use", "id": "t1", "name": "pretrazi", "input": {"upit": "PDV rok"}},
        ], "model": body["model"], "usage": {}}

    r = LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}], tools=_TOOLS)
    assert r.text == "gledam"
    assert r.tool_calls == [{"name": "pretrazi", "args": {"upit": "PDV rok"}}]


def test_anthropic_no_tools_unchanged(cfg):
    # bez tools= poziv mora ostati bajt-identičan starom ponašanju (nema "tools" u body, tool_calls=[]).
    cfg.llm_base_url = "https://api.anthropic.com"; cfg.llm_api_key = "k"; cfg.llm_model = "claude-sonnet-5"
    seen = {}

    def transport(url, headers, body):
        seen["body"] = body
        return {"content": [{"type": "text", "text": "odgovor"}], "model": body["model"], "usage": {}}

    r = LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}])
    assert "tools" not in seen["body"]
    assert r.tool_calls == []


def test_openai_tools_sent_in_provider_format(cfg):
    cfg.llm_base_url = "https://api.deepseek.com"; cfg.llm_api_key = "k"; cfg.llm_model = "deepseek-chat"
    seen = {}

    def transport(url, headers, body):
        seen["body"] = body
        return {"choices": [{"message": {"content": "odgovor"}}], "model": body["model"], "usage": {}}

    LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}], tools=_TOOLS)
    assert seen["body"]["tools"] == [
        {"type": "function", "function": {
            "name": "pretrazi", "description": "Pretraži.",
            "parameters": {"type": "object", "properties": {"upit": {"type": "string"}},
                            "required": ["upit"]}}}
    ]
    assert seen["body"]["tool_choice"] == "auto"


def test_openai_tool_calls_parsed(cfg):
    cfg.llm_base_url = "https://api.deepseek.com"; cfg.llm_api_key = "k"; cfg.llm_model = "deepseek-chat"

    def transport(url, headers, body):
        return {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "pretrazi", "arguments": '{"upit": "PDV rok"}'}}],
        }}], "model": body["model"], "usage": {}}

    r = LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}], tools=_TOOLS)
    assert r.tool_calls == [{"name": "pretrazi", "args": {"upit": "PDV rok"}}]


def test_openai_no_tools_unchanged(cfg):
    cfg.llm_base_url = "https://api.deepseek.com"; cfg.llm_api_key = "k"; cfg.llm_model = "deepseek-chat"
    seen = {}

    def transport(url, headers, body):
        seen["body"] = body
        return {"choices": [{"message": {"content": "odgovor"}}], "model": body["model"], "usage": {}}

    r = LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}])
    assert "tools" not in seen["body"] and "tool_choice" not in seen["body"]
    assert r.tool_calls == []


def test_openai_malformed_tool_call_args_falls_back_to_empty(cfg):
    # nevažeći JSON u function.arguments -> ne rušiti, args={} uz zadržan text/name.
    cfg.llm_base_url = "https://api.deepseek.com"; cfg.llm_api_key = "k"; cfg.llm_model = "deepseek-chat"

    def transport(url, headers, body):
        return {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "pretrazi", "arguments": "not-json"}}],
        }}], "model": body["model"], "usage": {}}

    r = LLMClient(cfg, transport=transport).complete([{"role": "user", "content": "hej"}], tools=_TOOLS)
    assert r.tool_calls == [{"name": "pretrazi", "args": {}}]


def test_supports_tools(cfg):
    cfg.llm_base_url = "https://api.anthropic.com"; cfg.llm_api_key = "k"; cfg.llm_model = "x"
    assert LLMClient(cfg).supports_tools() is True

    cfg.llm_base_url = "https://api.deepseek.com"
    assert LLMClient(cfg).supports_tools() is True

    cfg.llm_provider = "ollama"
    assert LLMClient(cfg).supports_tools() is True
