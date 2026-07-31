import pytest
from ragspine.config import Config, set_config

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    c = Config.from_env(); set_config(c)
    yield c
    set_config(None)
