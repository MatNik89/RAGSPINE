"""Routing-lanac s fallbackom: primarni + fallback provideri; na grešku prelazi
na sljedeći. Ključevi šifrirani; endpoint admin-only."""
import pytest

from atlas.business import model_settings as ms
from atlas.core.llm import FallbackLLM, LLMError, LLMResult, LLMUnavailable


class _FakeClient:
    def __init__(self, fail=False, tag="x"):
        self.fail, self.tag = fail, tag

    def supports_tools(self):
        return True

    def complete(self, *a, **k):
        if self.fail:
            raise LLMUnavailable("pao")
        return LLMResult(text=self.tag, model=self.tag, usage={}, tool_calls=[])


def test_fallback_uses_second_when_first_fails():
    f = FallbackLLM([None, None])
    f._clients = [_FakeClient(fail=True), _FakeClient(fail=False, tag="drugi")]
    res = f.complete([{"role": "user", "content": "x"}])
    assert res.text == "drugi" and f.last_used == 1


def test_fallback_first_wins_when_ok():
    f = FallbackLLM([None, None])
    f._clients = [_FakeClient(fail=False, tag="prvi"), _FakeClient(fail=False, tag="drugi")]
    assert f.complete([]).text == "prvi" and f.last_used == 0


def test_fallback_all_fail_raises_last():
    f = FallbackLLM([None])
    f._clients = [_FakeClient(fail=True), _FakeClient(fail=True)]
    with pytest.raises(LLMUnavailable):
        f.complete([])


def test_set_and_get_fallbacks_masks_key(spine, cfg):
    ms.set_fallbacks(spine, [
        {"provider": "ollama", "model": "llama3", "ollama_url": "http://127.0.0.1:11434"},
        {"provider": "deepseek", "model": "deepseek-chat",
         "base_url": "https://api.deepseek.com", "api_key": "TAJNI123"},
    ], cfg)
    ui = ms.get_fallbacks(spine)
    assert [p["provider"] for p in ui] == ["ollama", "deepseek"]
    assert ui[1]["has_api_key"] is True and "TAJNI123" not in str(ui)  # ključ maskiran


def test_fallbacks_key_encrypted_at_rest(spine, cfg):
    ms.set_fallbacks(spine, [{"provider": "deepseek", "base_url": "https://api.deepseek.com",
                              "api_key": "TAJNI123"}], cfg)
    raw = spine.get_override("model", "fallbacks", "")
    assert "TAJNI123" not in raw  # šifriran u bazi
    dec = ms.fallbacks(spine, cfg)
    assert dec[0]["api_key"] == "TAJNI123"  # dešifrira se za lanac


def test_chain_is_primary_plus_fallbacks(spine, cfg):
    ms.save(spine, "ollama", model="prim", ollama_url="http://127.0.0.1:11434")
    ms.set_fallbacks(spine, [{"provider": "ollama", "model": "fb1",
                              "ollama_url": "http://127.0.0.1:11434"}], cfg)
    ch = ms.chain(spine, cfg)
    assert len(ch) == 2 and ch[0].llm_model == "prim" and ch[1].llm_model == "fb1"


def test_set_fallbacks_rejects_unknown_provider(spine, cfg):
    with pytest.raises(ValueError):
        ms.set_fallbacks(spine, [{"provider": "izmisljeno"}], cfg)


def test_endpoint_admin_only(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from atlas.business import tenancy
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    ho = {"Authorization": "Bearer " + c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]}
    add_user(spine, "m", "pw")
    tm = c.post("/auth/login", json={"username": "m", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='m'").fetchone()["id"], "member")
    body = {"profiles": [{"provider": "ollama", "model": "x", "ollama_url": "http://127.0.0.1:11434"}]}
    assert c.post("/model/fallbacks", headers={"Authorization": f"Bearer {tm}"}, json=body).status_code == 403
    assert c.post("/model/fallbacks", headers=ho, json=body).status_code == 200
    assert c.get("/model/fallbacks", headers=ho).json()["fallbacks"][0]["provider"] == "ollama"
