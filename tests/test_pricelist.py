from fastapi.testclient import TestClient

from atlas.business import pricelist
from atlas.web.api import create_app
from atlas.web.deps import add_user


def _client_row(spine, name="Alfa", oib="1", pausal_eur=0, pdv_status="nije u pdvu"):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO clients(name, oib, pausal_eur, pdv_status) VALUES(?,?,?,?)",
            (name, oib, pausal_eur, pdv_status),
        )
        return cur.lastrowid


def test_seed_inserts_9_then_zero(spine):
    n = pricelist.seed(spine)
    assert n == 9
    assert pricelist.seed(spine) == 0


def test_get_price_known_and_unknown_default(spine):
    pricelist.seed(spine)
    assert pricelist.get_price(spine, "mjesecno_knjigovodstvo") > 0
    assert pricelist.get_price(spine, "ne-postoji-xyz", default=42.0) == 42.0


def test_price_list_has_9_items(spine):
    pricelist.seed(spine)
    rows = pricelist.price_list(spine)
    assert len(rows) == 9


def test_izracunaj_cijenu_pdv_client_with_employees(spine):
    pricelist.seed(spine)
    cid = _client_row(spine, pausal_eur=200, pdv_status="u sustavu PDV-a")
    result = pricelist.calculate_price(spine, cid, employees=3)

    per_emp = pricelist.get_price(spine, "obracun_place")
    pdv = pricelist.get_price(spine, "pdv_prijava")
    joppd = pricelist.get_price(spine, "joppd_obrazac")
    expected = round(200 + 3 * per_emp + pdv + joppd, 2)

    assert float(result["ukupno"]) == expected
    assert result["klijent"] == "Alfa"
    assert any("PDV" in s["naziv"] for s in result["stavke"])
    assert any("JOPPD" in s["naziv"] for s in result["stavke"])
    # itemized stavke must sum to the reported total (Decimal 2dp)
    total = sum((s["iznos"] for s in result["stavke"]))
    assert total == result["ukupno"]
    assert str(result["ukupno"]) == f"{expected:.2f}"


def test_izracunaj_cijenu_non_pdv_client_no_pdv_line(spine):
    pricelist.seed(spine)
    cid = _client_row(spine, pausal_eur=100, pdv_status="nije u pdvu")
    result = pricelist.calculate_price(spine, cid)
    assert not any("PDV" in s["naziv"] for s in result["stavke"])


def test_izracunaj_cijenu_uses_default_base_when_no_pausal(spine):
    pricelist.seed(spine)
    cid = _client_row(spine, pausal_eur=0, pdv_status="nije u pdvu")
    result = pricelist.calculate_price(spine, cid)
    default_base = pricelist.get_price(spine, "mjesecno_knjigovodstvo")
    assert float(result["ukupno"]) == default_base


def test_usporedi_ispod_trzista(spine):
    c100 = _client_row(spine, "A", "1", pausal_eur=100)
    _client_row(spine, "B", "2", pausal_eur=200)
    _client_row(spine, "C", "3", pausal_eur=300)

    res = pricelist.compare_to_market(spine, c100)
    assert float(res["prosjek_trzista"]) == 250.0
    low = res["preporuka"].lower()
    assert "ispod" in low or "poveć" in low


def test_usporedi_u_skladu_s_trzistem(spine):
    c1 = _client_row(spine, "A", "1", pausal_eur=200)
    _client_row(spine, "B", "2", pausal_eur=200)
    _client_row(spine, "C", "3", pausal_eur=200)

    res = pricelist.compare_to_market(spine, c1)
    assert "sklad" in res["preporuka"].lower()


def test_usporedi_no_other_clients_is_neutral(spine):
    cid = _client_row(spine, "A", "1", pausal_eur=100)
    res = pricelist.compare_to_market(spine, cid)
    assert res["prosjek_trzista"] is None
    assert res["preporuka"]


def _auth_headers(c, spine):
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_api_cjenik_get_list(spine, cfg):
    pricelist.seed(spine)
    c = TestClient(create_app(spine, cfg))
    headers = _auth_headers(c, spine)
    r = c.get("/cjenik", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 9


def test_api_cjenik_izracun(spine, cfg):
    pricelist.seed(spine)
    cid = _client_row(spine, pausal_eur=150, pdv_status="u sustavu PDV-a")
    c = TestClient(create_app(spine, cfg))
    headers = _auth_headers(c, spine)
    r = c.post("/cjenik/izracun", json={"client_id": cid, "employees": 2}, headers=headers)
    assert r.status_code == 200
    assert r.json()["ukupno"] > 0


def test_api_cjenik_usporedba(spine, cfg):
    cid = _client_row(spine, pausal_eur=150)
    _client_row(spine, "B", "2", pausal_eur=300)
    c = TestClient(create_app(spine, cfg))
    headers = _auth_headers(c, spine)
    r = c.get(f"/cjenik/usporedba/{cid}", headers=headers)
    assert r.status_code == 200
    assert "preporuka" in r.json()


def test_api_set_pausal_then_used_in_izracun(spine, cfg):
    pricelist.seed(spine)
    cid = _client_row(spine, pausal_eur=0, pdv_status="nije u pdvu")
    c = TestClient(create_app(spine, cfg))
    headers = _auth_headers(c, spine)

    r = c.post(f"/clients/{cid}/pausal", json={"pausal_eur": 500}, headers=headers)
    assert r.status_code == 200

    r = c.post("/cjenik/izracun", json={"client_id": cid}, headers=headers)
    assert r.json()["ukupno"] == 500.0
