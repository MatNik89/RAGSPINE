import pytest

from ragspine.business.dnevnice import obracun, RATES


def test_full_and_half_days():
    r = obracun("Njemačka", 3, 1)
    assert r["ukupno_dnevnice"] == 3.5 * RATES["Njemačka"]
    assert r["dnevnica_iznos"] == RATES["Njemačka"]
    assert r["country"] == "Njemačka"


def test_override_changes_rate(spine):
    spine.set_override("dnevnica", "Njemačka", "90")
    r = obracun("Njemačka", 3, 1, spine=spine)
    assert r["dnevnica_iznos"] == 90.0
    assert r["ukupno_dnevnice"] == 3.5 * 90.0


def test_smjestaj_prijevoz_added():
    r = obracun("Hrvatska", 2, 0, smjestaj=50.0, prijevoz=10.0)
    assert r["ukupno"] == r["ukupno_dnevnice"] + 50.0 + 10.0


def test_unknown_country_raises():
    with pytest.raises(ValueError):
        obracun("Nepostojeća", 1)
