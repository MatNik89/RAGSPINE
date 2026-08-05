from ragspine.core.spine import init_spine
from ragspine.ops import wizard_state as ws


def _spine(tmp_path):
    return init_spine(str(tmp_path / "t.db"))


def test_stage_defaults_zero(tmp_path):
    s = _spine(tmp_path)
    assert ws.get_stage(s) == 0
    assert ws.is_complete(s) is False


def test_stage_roundtrip_and_complete(tmp_path):
    s = _spine(tmp_path)
    ws.set_stage(s, 2)
    assert ws.get_stage(s) == 2
    ws.set_stage(s, 3)  # upsert, ne duplira
    assert ws.get_stage(s) == 3
    ws.mark_complete(s)
    assert ws.is_complete(s) is True


def test_reset_clears(tmp_path):
    s = _spine(tmp_path)
    ws.set_stage(s, 4)
    ws.mark_complete(s)
    ws.reset(s)
    assert ws.get_stage(s) == 0
    assert ws.is_complete(s) is False
