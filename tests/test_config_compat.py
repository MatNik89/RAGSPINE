"""Compat sloj renamea: RAGSPINE_* env aliasi + ~/.ragspine fallback."""  # compat: ragspine
import os
from pathlib import Path

from atlas import config


def test_env_atlas_ima_prednost(monkeypatch):
    monkeypatch.setenv("ATLAS_HOST", "1.2.3.4")
    monkeypatch.setenv("RAGSPINE_HOST", "5.6.7.8")  # compat: ragspine
    assert config._env("HOST", "x") == "1.2.3.4"


def test_env_stari_alias_radi(monkeypatch):
    monkeypatch.delenv("ATLAS_HOST", raising=False)
    monkeypatch.setenv("RAGSPINE_HOST", "5.6.7.8")  # compat: ragspine
    assert config._env("HOST", "x") == "5.6.7.8"


def test_env_default(monkeypatch):
    monkeypatch.delenv("ATLAS_HOST", raising=False)
    monkeypatch.delenv("RAGSPINE_HOST", raising=False)  # compat: ragspine
    assert config._env("HOST", "x") == "x"


def test_data_dir_fallback_na_stari(monkeypatch, tmp_path):
    """~/.atlas ne postoji, ~/.ragspine postoji → koristi stari."""  # compat: ragspine
    legacy = tmp_path / ".ragspine"  # compat: ragspine
    legacy.mkdir()
    monkeypatch.setattr(config, "_home", lambda: str(tmp_path))
    assert config.default_data_dir() == str(legacy)


def test_data_dir_novi_kad_nema_starog(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_home", lambda: str(tmp_path))
    assert config.default_data_dir() == str(tmp_path / ".atlas")


def test_data_dir_novi_ima_prednost(monkeypatch, tmp_path):
    (tmp_path / ".atlas").mkdir()
    (tmp_path / ".ragspine").mkdir()  # compat: ragspine
    monkeypatch.setattr(config, "_home", lambda: str(tmp_path))
    assert config.default_data_dir() == str(tmp_path / ".atlas")


def test_db_fallback_na_stari(monkeypatch, tmp_path):
    """Postojeći ragspine.db u data diru se koristi; inače atlas.db."""  # compat: ragspine
    for k in ("ATLAS_DATA_DIR", "ATLAS_DB_PATH", "ATLAS_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.replace("ATLAS", "RAGSPINE"), raising=False)  # compat: ragspine
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    (tmp_path / "ragspine.db").touch()  # compat: ragspine
    cfg = config.Config.from_env()
    assert cfg.db_path == str(tmp_path / "ragspine.db")  # compat: ragspine


def test_db_novi_default(monkeypatch, tmp_path):
    for k in ("ATLAS_DATA_DIR", "ATLAS_DB_PATH", "ATLAS_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.replace("ATLAS", "RAGSPINE"), raising=False)  # compat: ragspine
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    cfg = config.Config.from_env()
    assert cfg.db_path == str(tmp_path / "atlas.db")
