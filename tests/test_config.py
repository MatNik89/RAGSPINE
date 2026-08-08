import os
from atlas.config import Config

def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.port == 8400
    assert cfg.db_path == str(tmp_path / "atlas.db")

def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PORT", "9000")
    assert Config.from_env().port == 9000

def test_llm_path_env_default_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    assert Config.from_env().llm_path == ""

def test_llm_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_LLM_PATH", "/v1beta/openai/chat/completions")
    assert Config.from_env().llm_path == "/v1beta/openai/chat/completions"

def test_jwt_secret_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    s1 = Config.from_env().jwt_secret
    s2 = Config.from_env().jwt_secret
    assert s1 == s2 and len(s1) >= 32

def test_https_only_default_false(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    assert Config.from_env().https_only is False

def test_https_only_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_HTTPS_ONLY", "1")
    assert Config.from_env().https_only is True


def test_ocr_langs_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    from atlas.config import Config
    assert Config.from_env().ocr_langs == "hrv+eng"


def test_data_dir_normpath_bez_mijesanih_crta(monkeypatch, tmp_path):
    """E2E kozmetika: 'C:\\Users\\X/.atlas' — normpath izravnava separatore."""
    for k in ("ATLAS_DATA_DIR", "RAGSPINE_DATA_DIR"):  # compat: ragspine
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path) + "/./poddir")
    monkeypatch.delenv("ATLAS_JWT_SECRET", raising=False)
    monkeypatch.delenv("RAGSPINE_JWT_SECRET", raising=False)  # compat: ragspine
    from atlas import config
    cfg = config.Config.from_env()
    assert "/./" not in cfg.data_dir
    assert cfg.data_dir == os.path.normpath(cfg.data_dir)
