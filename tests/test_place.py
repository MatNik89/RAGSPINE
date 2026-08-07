import pytest

from atlas.business.place import bruto_to_neto

def test_basic_2000():
    r = bruto_to_neto(2000.0)
    assert r["doprinosi"] == 400.0
    assert r["osnovica"] == 1000.0          # 1600 dohodak - 600 odbitak
    assert r["porez"] == 200.0              # 20%
    assert r["neto"] == 1400.0

def test_high_income_upper_band():
    r = bruto_to_neto(10000.0)
    # dohodak 8000, osnovica 7400: 5000*0.2 + 2400*0.3 = 1720
    assert r["porez"] == 1720.0

def test_children_allowance():
    r = bruto_to_neto(2000.0, children=2)
    assert r["odbitak"] == 600 + 300 + 420

def test_city_override(spine):
    spine.set_override("kalkulator", "porez_niza.Split", "21.5")
    r = bruto_to_neto(2000.0, city="Split", spine=spine)
    assert r["stopa_niza"] == 21.5

def test_legacy_prirez_override(spine):
    spine.set_override("kalkulator", "prirez.Sisak", "10")
    r = bruto_to_neto(2000.0, city="Sisak", spine=spine)
    assert r["porez"] == 220.0              # 200 + 10%

def test_negative_bruto_raises():
    with pytest.raises(ValueError):
        bruto_to_neto(-100)

def test_override_percent_sign_and_comma_decimal(spine):
    spine.set_override("kalkulator", "porez_niza.Split", "21,5%")
    r = bruto_to_neto(2000.0, city="Split", spine=spine)
    assert r["stopa_niza"] == 21.5

def test_garbage_prirez_override_treated_as_absent(spine):
    spine.set_override("kalkulator", "prirez.Sisak", "garbage")
    r = bruto_to_neto(2000.0, city="Sisak", spine=spine)
    assert r["porez"] == 200.0
