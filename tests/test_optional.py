from atlas.core import optional

def test_need_present():
    assert optional.need("json", "test-feature") is not None

def test_need_missing_registers():
    assert optional.need("nepostojeci_modul_xyz", "vektorska pretraga") is None
    assert "vektorska pretraga" in optional.missing()
