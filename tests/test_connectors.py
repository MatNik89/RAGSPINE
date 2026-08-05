"""Connector framework: registry, test-before-save, status, maskiranje tajni."""
import pytest
from ragspine.business import connectors as cx


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(cx._TYPES)
    yield
    cx._TYPES.clear()
    cx._TYPES.update(saved)


def _fake_type(kind="fake", result=("connected", "ok")):
    return cx.ConnectorType(
        kind=kind, label="Fake", category="kanal",
        fields=[cx.Field("token", "Token", type="password", secret=True),
                cx.Field("host", "Host")],
        test=lambda cfg: result)


def test_list_types_includes_registered():
    cx.register(_fake_type())
    kinds = {t["kind"] for t in cx.list_types()}
    assert "fake" in kinds
    fields = next(t for t in cx.list_types() if t["kind"] == "fake")["fields"]
    assert any(f["secret"] for f in fields)


def test_test_draft_validates_required():
    cx.register(_fake_type())
    # obavezno polje prazno/izostavljeno → ValueError PRIJE testa
    with pytest.raises(ValueError):
        cx.test_draft("fake", {"token": "", "host": "x"})
    with pytest.raises(ValueError):
        cx.test_draft("fake", {"host": "x"})
    ok = cx.test_draft("fake", {"token": "t", "host": "x"})
    assert ok["status"] == "connected"


def test_test_draft_isolates_adapter_error():
    def boom(cfg):
        raise RuntimeError("puklo")
    cx.register(cx.ConnectorType(kind="boom", label="B", fields=[], test=boom))
    r = cx.test_draft("boom", {})
    assert r["status"] == "error" and "puklo" in r["detail"]


def test_create_connected_saves_and_masks_secret(spine):
    cx.register(_fake_type())
    res = cx.create(spine, "fake", "Moj kanal", {"token": "tajna123", "host": "h"}, user="ana")
    assert res["status"] == "connected"
    lst = cx.list_connectors(spine)
    assert len(lst) == 1 and lst[0]["name"] == "Moj kanal"
    assert lst[0]["config"]["token"] == "••••"      # tajna maskirana
    assert lst[0]["config"]["host"] == "h"


def test_create_error_still_saved_with_status_error(spine):
    cx.register(_fake_type(result=("error", "kriv token")))
    res = cx.create(spine, "fake", "Loš", {"token": "x", "host": "h"})
    assert res["status"] == "error"
    c = cx.list_connectors(spine)[0]
    assert c["status"] == "error" and c["last_error"] == "kriv token"


def test_create_pending_for_oauth_qr(spine):
    cx.register(_fake_type(result=("pending", "skeniraj QR")))
    res = cx.create(spine, "fake", "TG", {"token": "x", "host": "h"})
    assert res["status"] == "pending"  # ne prikazuje se kao spojen dok se ne autorizira


def test_status_and_delete(spine):
    cx.register(_fake_type())
    cid = cx.create(spine, "fake", "K", {"token": "x", "host": "h"})["id"]
    cx.set_status(spine, cid, "disabled", user="ana")
    assert cx.get(spine, cid)["status"] == "disabled"
    cx.delete(spine, cid)
    assert cx.get(spine, cid) is None


def test_unknown_kind_rejected(spine):
    with pytest.raises(ValueError):
        cx.create(spine, "nepostoji", "x", {})


def _admin(spine, cfg):
    from fastapi.testclient import TestClient
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "pw")
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_builtin_types_registered(spine, cfg):
    c, h = _admin(spine, cfg)  # create_app registrira builtin tipove
    kinds = {t["kind"] for t in c.get("/connector-types", headers=h).json()}
    assert {"mail_exchange", "mail_graph", "telegram", "whatsapp"} <= kinds
    assert "viber" not in kinds  # namjerno izbačen


def test_connector_routes_admin_flow(spine, cfg):
    c, h = _admin(spine, cfg)
    # telegram test bez telethona → error/pending, ali validacija radi
    t = c.post("/connectors/test", json={"kind": "telegram",
              "config": {"api_id": "123", "api_hash": "x", "phone": "+385"}}, headers=h)
    assert t.status_code == 200 and t.json()["status"] in ("pending", "error")
    # kreiraj mail_exchange (email format ok → pending)
    r = c.post("/connectors", json={"kind": "mail_exchange", "name": "Ured mail",
              "config": {"email": "ured@firma.hr", "password": "tajna"}}, headers=h)
    assert r.status_code == 200
    lst = c.get("/connectors", headers=h).json()
    assert lst[0]["name"] == "Ured mail" and lst[0]["config"]["password"] == "••••"
    cid = lst[0]["id"]
    # ukloni
    assert c.delete(f"/connectors/{cid}", headers=h).status_code == 200
    assert c.get("/connectors", headers=h).json() == []
    # UI
    assert c.get("/ui/kanali", headers=h).status_code == 200


def test_connector_routes_worker_forbidden(spine, cfg):
    c, h = _admin(spine, cfg)
    from ragspine.web.deps import add_user
    add_user(spine, "boris", "pw", "radnik")
    wt = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    wh = {"Authorization": f"Bearer {wt}"}
    assert c.get("/connectors", headers=wh).status_code == 403
    assert c.post("/connectors", json={"kind": "telegram", "name": "x", "config": {}}, headers=wh).status_code == 403
