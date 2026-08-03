from fastapi.testclient import TestClient

from ragspine.business import model_settings
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


# ---------- business ----------

def test_save_and_get_masks_api_key(spine):
    model_settings.save(spine, "anthropic", model="claude-x", api_key="secret-123", user="ana")
    g = model_settings.get(spine)
    assert g["provider"] == "anthropic" and g["model"] == "claude-x"
    assert g["has_api_key"] is True
    assert "secret-123" not in str(g)  # sirovi ključ se ne vraća


def test_blank_api_key_keeps_existing(spine):
    model_settings.save(spine, "openai", api_key="k1")
    model_settings.save(spine, "openai", model="gpt-x", api_key="")  # prazno = zadrži
    assert model_settings._raw(spine)["api_key"] == "k1"
    assert model_settings.get(spine)["model"] == "gpt-x"


def test_invalid_provider_rejected(spine):
    import pytest
    with pytest.raises(ValueError):
        model_settings.save(spine, "nonsense")


def test_apply_anthropic_overrides_cfg(spine, cfg):
    model_settings.save(spine, "anthropic", model="claude-x", api_key="key")
    applied = model_settings.apply(spine, cfg)
    assert applied.llm_api_key == "key" and applied.llm_model == "claude-x"
    assert "anthropic" in applied.llm_base_url


def test_apply_ollama_clears_key(spine, cfg):
    model_settings.save(spine, "ollama", model="llama3.1", base_url="http://x:11434")
    applied = model_settings.apply(spine, cfg)
    assert applied.llm_api_key == "" and applied.ollama_url == "http://x:11434"
    assert applied.llm_model == "llama3.1"


def test_apply_no_selection_returns_cfg_unchanged(spine, cfg):
    assert model_settings.apply(spine, cfg) is cfg


# ---------- endpoints + UI ----------

def test_model_endpoints_roundtrip(spine, cfg):
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.post("/model", json={"provider": "openai", "model": "gpt-x", "api_key": "sk-1"}, headers=_auth(tok))
    assert r.status_code == 200 and r.json()["has_api_key"] is True
    g = c.get("/model", headers=_auth(tok)).json()
    assert g["provider"] == "openai" and g["model"] == "gpt-x"
    assert "sk-1" not in r.text  # ključ se ne vraća u odgovoru


def test_model_bad_provider_400(spine, cfg):
    c = _client(spine, cfg); tok = _token(c, spine)
    assert c.post("/model", json={"provider": "x"}, headers=_auth(tok)).status_code == 400


def test_model_endpoints_need_auth(spine, cfg):
    c = _client(spine, cfg)
    assert c.get("/model").status_code == 401
    assert c.post("/model", json={"provider": "openai"}).status_code == 401


def test_model_test_endpoint_returns_status(spine, cfg):
    # bez konfiguriranog providera -> ok:false, ali ne 500
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.post("/model/test", headers=_auth(tok))
    assert r.status_code == 200 and "ok" in r.json()


def test_ui_model_page(spine, cfg):
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.get("/ui/model", headers=_auth(tok))
    assert r.status_code == 200
    assert "Model" in r.text and "/model" in r.text
    assert "Ollama" in r.text and "@font-face" in r.text and "innerHTML" not in r.text


def test_ui_model_no_auth_redirects(spine, cfg):
    c = _client(spine, cfg)
    assert c.get("/ui/model", follow_redirects=False).status_code == 303


def test_postavke_lists_model(spine, cfg):
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.get("/ui/postavke", headers=_auth(tok))
    assert "/ui/model" in r.text
