import pytest
from ragspine.config import Config, set_config
from ragspine.core.spine import Spine

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    c = Config.from_env(); set_config(c)
    yield c
    set_config(None)

@pytest.fixture
def spine(tmp_path):
    return Spine(str(tmp_path / "t.db"))
