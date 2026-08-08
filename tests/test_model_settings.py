from fastapi.testclient import TestClient

from atlas.business import model_settings
from atlas.business.model_settings import PROVIDER_CATALOG, PROVIDERS
from atlas.config import get_config
from atlas.web.api import create_app
from atlas.web.deps import add_user
from atlas.web.templates_model import model_page
from tests.conftest import complete_setup


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    complete_setup(spine)
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


# ---------- B11: katalog providera ----------

def test_provider_catalog_entries_valid():
    keys = [p["key"] for p in PROVIDER_CATALOG]
    assert len(keys) == len(set(keys)), "katalog ključevi moraju biti jedinstveni"
    for p in PROVIDER_CATALOG:
        assert set(("key", "naziv", "base_url", "path", "needs_key", "hint")) <= set(p)
        assert isinstance(p["needs_key"], bool)
        assert p["path"].startswith("/")

def test_providers_keeps_legacy_keys():
    assert PROVIDERS[:3] == ("anthropic", "openai", "ollama") or \
        {"anthropic", "openai", "ollama"} <= set(PROVIDERS)

def test_gemini_path_is_nonstandard():
    gemini = next(p for p in PROVIDER_CATALOG if p["key"] == "gemini")
    assert gemini["path"] == "/v1beta/openai/chat/completions"

def test_new_provider_still_validates_https(spine):
    import pytest
    with pytest.raises(ValueError):  # SSRF zaštita mora vrijediti i za nove providere
        model_settings.save(spine, "deepseek", base_url="http://192.168.1.5")

def test_apply_new_provider_uses_catalog_path(spine, cfg):
    model_settings.save(spine, "gemini", model="gemini-x", api_key="k")
    applied = model_settings.apply(spine, cfg)
    assert applied.llm_path == "/v1beta/openai/chat/completions"
    assert applied.llm_base_url == "https://generativelanguage.googleapis.com"

def test_apply_openai_default_path_unchanged(spine, cfg):
    model_settings.save(spine, "openai", model="gpt-x", api_key="k")
    applied = model_settings.apply(spine, cfg)
    assert applied.llm_path == "/v1/chat/completions"
    assert applied.llm_base_url == "https://api.openai.com"

def test_templates_model_lists_catalog_and_manual_entry():
    html = model_page()
    for p in PROVIDER_CATALOG:
        assert p["naziv"] in html
    assert "Ručni unos" in html


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


def test_remote_base_url_must_be_https_public(spine):
    import pytest
    with pytest.raises(ValueError):  # interni host — SSRF
        model_settings.save(spine, "openai", base_url="http://192.168.1.5:8080")
    with pytest.raises(ValueError):  # nije https
        model_settings.save(spine, "anthropic", base_url="http://api.anthropic.com")


def test_endpoint_change_invalidates_stored_key(spine):
    # spremi ključ na jedan endpoint, pa promijeni endpoint bez novog ključa →
    # stari ključ se NE smije zadržati (ne šalji ga na novi host)
    model_settings.save(spine, "openai", base_url="https://api.openai.com", api_key="sk-secret")
    assert model_settings._raw(spine)["api_key"] == "sk-secret"
    model_settings.save(spine, "ollama", base_url="http://127.0.0.1:11434")  # promjena providera
    assert model_settings._raw(spine)["api_key"] == ""


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


def test_startup_applies_embed_model_override_to_global_config(spine, cfg):
    # wizard/postavke spremaju embed_model preko model_settings.save; embed._get_model()
    # čita globalni get_config() izravno (nema pristup spineu) — pa create_app mora
    # primijeniti DB-odabir na global config pri konstrukciji, inače vektorska pretraga
    # tiho koristi env-default model.
    model_settings.save(spine, "ollama", embed_model="BAAI/bge-m3")
    create_app(spine, cfg)
    assert get_config().embed_model == "BAAI/bge-m3"


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
