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
