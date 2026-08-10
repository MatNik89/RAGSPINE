"""Connector framework: registry, test-before-save, status, maskiranje tajni."""
import pytest
from atlas.business import connectors as cx
from tests.conftest import complete_setup


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


def test_create_connected_saves_and_masks_secret(spine, cfg):
    cx.register(_fake_type())
    res = cx.create(spine, "fake", "Moj kanal", {"token": "tajna123", "host": "h"}, cfg=cfg, user="ana")
    assert res["status"] == "connected"
    lst = cx.list_connectors(spine)
    assert len(lst) == 1 and lst[0]["name"] == "Moj kanal"
    assert lst[0]["config"]["token"] == "••••"      # tajna maskirana
    assert lst[0]["config"]["host"] == "h"


def test_create_error_still_saved_with_status_error(spine, cfg):
    cx.register(_fake_type(result=("error", "kriv token")))
    res = cx.create(spine, "fake", "Loš", {"token": "x", "host": "h"}, cfg=cfg)
    assert res["status"] == "error"
    c = cx.list_connectors(spine)[0]
    assert c["status"] == "error" and c["last_error"] == "kriv token"


def test_create_pending_for_oauth_qr(spine, cfg):
    cx.register(_fake_type(result=("pending", "skeniraj QR")))
    res = cx.create(spine, "fake", "TG", {"token": "x", "host": "h"}, cfg=cfg)
    assert res["status"] == "pending"  # ne prikazuje se kao spojen dok se ne autorizira


def test_status_and_delete(spine, cfg):
    cx.register(_fake_type())
    cid = cx.create(spine, "fake", "K", {"token": "x", "host": "h"}, cfg=cfg)["id"]
    cx.set_status(spine, cid, "disabled", user="ana")
    assert cx.get(spine, cid)["status"] == "disabled"
    cx.delete(spine, cid)
    assert cx.get(spine, cid) is None


def test_unknown_kind_rejected(spine):
    with pytest.raises(ValueError):
        cx.create(spine, "nepostoji", "x", {})


def _admin(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_builtin_types_registered(spine, cfg):
    c, h = _admin(spine, cfg)  # create_app registrira builtin tipove
    kinds = {t["kind"] for t in c.get("/connector-types", headers=h).json()}
    assert {"mail_imap", "mail_exchange", "mail_graph", "telegram_gateway"} <= kinds  # mail + TG


def test_connector_routes_admin_flow(spine, cfg):
    c, h = _admin(spine, cfg)
    # mail_exchange test (email format ok → pending, bez exchangeliba → error)
    t = c.post("/connectors/test", json={"kind": "mail_exchange",
              "config": {"email": "a@b.hr", "password": "x"}}, headers=h)
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
    assert c.get("/ui/posta", headers=h).status_code == 200


def test_connector_routes_worker_forbidden(spine, cfg):
    c, h = _admin(spine, cfg)
    from atlas.web.deps import add_user
    add_user(spine, "boris", "pw", "radnik")
    wt = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    wh = {"Authorization": f"Bearer {wt}"}
    assert c.get("/connectors", headers=wh).status_code == 403
    assert c.post("/connectors", json={"kind": "mail_exchange", "name": "x", "config": {}}, headers=wh).status_code == 403


def test_secret_encrypted_at_rest_and_decrypt_for_adapter(spine, cfg):
    import json
    cx.register(_fake_type())
    cid = cx.create(spine, "fake", "K", {"token": "supertajna", "host": "h"}, cfg=cfg, org_id=1)["id"]
    # u DB-u je token ŠIFRIRAN (enc:), ne plaintext
    raw = spine.read().execute("SELECT config_json FROM connectors WHERE id=?", (cid,)).fetchone()[0]
    stored = json.loads(raw)
    assert stored["token"].startswith("enc:") and "supertajna" not in raw
    # config_for_adapter dešifrira za server-side spajanje
    kind, decrypted = cx.config_for_adapter(spine, cid, cfg, org_id=1)
    assert decrypted["token"] == "supertajna" and decrypted["host"] == "h"


def test_org_isolation(spine, cfg):
    cx.register(_fake_type())
    cx.create(spine, "fake", "orgA", {"token": "x", "host": "h"}, cfg=cfg, org_id=1)
    cx.create(spine, "fake", "orgB", {"token": "y", "host": "h"}, cfg=cfg, org_id=2)
    a = cx.list_connectors(spine, org_id=1)
    assert len(a) == 1 and a[0]["name"] == "orgA"
    # org 2 ne vidi org 1 konektor; ne može ga ni dohvatiti
    b_id = cx.list_connectors(spine, org_id=2)[0]["id"]
    assert cx.get(spine, b_id, org_id=1) is None


def test_set_status_admin_only_disable(spine, cfg):
    cx.register(_fake_type())
    cid = cx.create(spine, "fake", "K", {"token": "x", "host": "h"}, cfg=cfg, org_id=1)["id"]
    cx.set_status(spine, cid, "disabled", org_id=1)
    assert cx.get(spine, cid, org_id=1)["status"] == "disabled"
    # admin NE smije ručno postaviti 'connected' (to radi adapter)
    with pytest.raises(ValueError):
        cx.set_status(spine, cid, "connected", org_id=1)
