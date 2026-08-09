"""User-defined polja obveza: registar (obligation_fields) + JSON meta na
obligations; core stupci zaključani; AI/UI pune vrijednosti, ne diraju strukturu."""
import pytest
from fastapi.testclient import TestClient

from atlas.business import obligation_fields as of
from atlas.business import obveze, tenancy
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _obl(spine):
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name) VALUES('Pekara')").lastrowid
        return c.execute("INSERT INTO obligations(client_id,kind,period) VALUES(?,?,?)",
                         (cid, "PDV", "2026-08")).lastrowid


def test_add_list_remove_field(spine):
    of.add_field(spine, "Iznos EUR", "number", user="a")
    fields = of.list_fields(spine)
    assert fields[0]["key"] == "iznos_eur" and fields[0]["type"] == "number"
    assert fields[0]["label"] == "Iznos EUR"
    of.remove_field(spine, "iznos_eur")
    assert of.list_fields(spine) == []


def test_core_keys_locked(spine):
    for bad in ("kind", "period", "sent", "sent_by", "sent_at", "client", "client_id"):
        with pytest.raises(ValueError):
            of.add_field(spine, bad, "text", user="a")


def test_field_type_validated(spine):
    with pytest.raises(ValueError):
        of.add_field(spine, "x", "kolumna", user="a")  # nepoznat tip
    with pytest.raises(ValueError):
        of.add_field(spine, "", "text", user="a")  # prazan key


def test_set_value_requires_registered_field(spine):
    oid = _obl(spine)
    with pytest.raises(ValueError):
        of.set_value(spine, oid, "nema_polja", "x")  # nije u registru
    of.add_field(spine, "napomena", "text", user="a")
    of.set_value(spine, oid, "napomena", "hitno")
    assert of.get_meta(spine, oid)["napomena"] == "hitno"


def test_set_value_coerces_and_validates_type(spine):
    oid = _obl(spine)
    of.add_field(spine, "iznos", "number", user="a")
    of.add_field(spine, "rok", "date", user="a")
    of.set_value(spine, oid, "iznos", "12.5")
    of.set_value(spine, oid, "rok", "2026-08-20")
    meta = of.get_meta(spine, oid)
    assert meta["iznos"] == 12.5 and meta["rok"] == "2026-08-20"
    with pytest.raises(ValueError):
        of.set_value(spine, oid, "iznos", "nije broj")
    with pytest.raises(ValueError):
        of.set_value(spine, oid, "rok", "32.13.2026")


def test_number_rejects_nan_infinity(spine):
    # Codex: NaN/Infinity bi se spremio pa srušio JSON odgovor (500)
    oid = _obl(spine)
    of.add_field(spine, "iznos", "number", user="a")
    for bad in ("NaN", "Infinity", "-inf"):
        with pytest.raises(ValueError):
            of.set_value(spine, oid, "iznos", bad)
    assert of.get_meta(spine, oid) == {}  # ništa otrovano nije spremljeno


def test_bool_rejects_garbage(spine):
    oid = _obl(spine)
    of.add_field(spine, "odobreno", "bool", user="a")
    with pytest.raises(ValueError):
        of.set_value(spine, oid, "odobreno", "definitivno")
    of.set_value(spine, oid, "odobreno", "da")
    assert of.get_meta(spine, oid)["odobreno"] == 1


def test_remove_field_keeps_stored_values_harmless(spine):
    oid = _obl(spine)
    of.add_field(spine, "napomena", "text", user="a")
    of.set_value(spine, oid, "napomena", "x")
    of.remove_field(spine, "napomena")  # ukloni definiciju
    # stara vrijednost u JSON-u ne ruši ništa; get_meta i dalje radi
    assert isinstance(of.get_meta(spine, oid), dict)


def test_list_period_includes_meta(spine):
    oid = _obl(spine)
    of.add_field(spine, "napomena", "text", user="a")
    of.set_value(spine, oid, "napomena", "hitno")
    rows = obveze.list_period(spine, "PDV", "2026-08")
    mine = [r for r in rows if r["obligation_id"] == oid][0]
    assert mine["meta"]["napomena"] == "hitno"


# --- API: definiranje polja = owner; punjenje vrijednosti = member+ ----------

def _login(spine, cfg, username, role):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, username, "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": username, "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, role)
    return c, {"Authorization": f"Bearer {tok}"}


def test_fields_crud_owner_only(spine, cfg):
    c, ho = _login(spine, cfg, "gazda", "owner")
    hm = _login(spine, cfg, "radnik", "member")[1]
    assert c.post("/obveze/polja", headers=hm, json={"label": "X", "type": "text"}).status_code == 403
    r = c.post("/obveze/polja", headers=ho, json={"label": "Napomena", "type": "text"})
    assert r.status_code == 200 and r.json()["key"] == "napomena"
    assert any(f["key"] == "napomena" for f in c.get("/obveze/polja", headers=hm).json())  # čitanje ok


def test_polja_page(spine, cfg):
    c, ho = _login(spine, cfg, "gazda", "owner")
    c.post("/auth/login", json={"username": "gazda", "password": "pw"})
    r = c.get("/ui/obveze-polja", headers=ho)
    assert r.status_code == 200 and "Polja obveza" in r.text


def test_set_field_value_endpoint_member(spine, cfg):
    c, ho = _login(spine, cfg, "gazda", "owner")
    c.post("/obveze/polja", headers=ho, json={"label": "Napomena", "type": "text"})
    oid = _obl(spine)
    hm = _login(spine, cfg, "radnik", "member")[1]
    r = c.post(f"/obveze/{oid}/polje", headers=hm, json={"key": "napomena", "value": "hitno"})
    assert r.status_code == 200
    assert of.get_meta(spine, oid)["napomena"] == "hitno"


def test_set_value_blocked_for_invisible_client(spine, cfg):
    # Codex IDOR: restringirani radnik ne smije pisati u obvezu tuđeg klijenta
    from atlas.business import client_visibility
    c, ho = _login(spine, cfg, "gazda", "owner")
    c.post("/obveze/polja", headers=ho, json={"label": "Napomena", "type": "text"})
    oid = _obl(spine)  # obveza klijenta "Pekara"
    # radnik s ograničenom vidljivošću (ne vidi nijednog klijenta)
    add_user(spine, "restr", "pw")
    tok = c.post("/auth/login", json={"username": "restr", "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username='restr'").fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, "member")
    with spine.write() as conn:
        conn.execute("UPDATE users SET sees_all_clients=0 WHERE id=?", (uid,))
    r = c.post(f"/obveze/{oid}/polje", headers={"Authorization": f"Bearer {tok}"},
               json={"key": "napomena", "value": "provaljeno"})
    assert r.status_code == 403
    assert of.get_meta(spine, oid) == {}  # ništa upisano


def test_number_nan_endpoint_400_not_500(spine, cfg):
    c, ho = _login(spine, cfg, "gazda", "owner")
    c.post("/obveze/polja", headers=ho, json={"label": "Iznos", "type": "number"})
    oid = _obl(spine)
    hm = _login(spine, cfg, "radnik", "member")[1]
    r = c.post(f"/obveze/{oid}/polje", headers=hm, json={"key": "iznos", "value": "NaN"})
    assert r.status_code == 400  # ne 500, i ništa nije spremljeno
    assert of.get_meta(spine, oid) == {}
