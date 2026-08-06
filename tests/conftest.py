import pytest
from ragspine.config import Config, set_config
from ragspine.core.spine import Spine


@pytest.fixture(autouse=True)
def _reset_embed_globals():
    """embed drži učitani model u modul-globalu; bez reseta procuri između
    testova (npr. fake iz test_embed_download u kasnije retrieval testove)."""
    from ragspine.rag import embed
    embed._model = None
    embed._model_failed = False
    yield
    embed._model = None
    embed._model_failed = False

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    c = Config.from_env(); set_config(c)
    yield c
    set_config(None)

@pytest.fixture
def spine(tmp_path):
    return Spine(str(tmp_path / "t.db"))


def complete_setup(spine):
    """Gatekeeper drži web na /ui/setup dok setup wizard ne završi — testovi
    koji gađaju API/UI rute ovim označe setup dovršenim (jedno mjesto za
    incantation umjesto raspršenih wizard_state.mark_complete poziva)."""
    from ragspine.ops import wizard_state
    wizard_state.mark_complete(spine)
