"""Faza 5 T2: agent HTTP protokol (poll/result) + izdavanje tokena + enqueue."""
from fastapi.testclient import TestClient

from atlas.business import devices, fleet, tenancy
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _owner(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _role(spine, cfg, c, username, role):
    add_user(spine, username, "pw")
    tok = c.post("/auth/login", json={"username": username, "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, role)
    return {"Authorization": f"Bearer {tok}"}


def _dev(spine):
    return devices.add_device(spine, "radna-stanica", "PC-Ana", user="a",
                              host="192.168.1.10")["id"]


# --- token izdavanje (owner) -----------------------------------------------

def test_token_issue_owner_only(spine, cfg):
    c, ho = _owner(spine, cfg)
    did = _dev(spine)
    ha = _role(spine, cfg, c, "admin1", "admin")
    assert c.post(f"/uredjaji/{did}/token", headers=ha).status_code == 403
    r = c.post(f"/uredjaji/{did}/token", headers=ho)
    assert r.status_code == 200 and r.json()["token"].startswith(f"{did}.")


def test_token_revoke_owner(spine, cfg):
    c, ho = _owner(spine, cfg)
    did = _dev(spine)
    tok = c.post(f"/uredjaji/{did}/token", headers=ho).json()["token"]
    assert fleet.verify_token(spine, tok) == did
    assert c.request("DELETE", f"/uredjaji/{did}/token", headers=ho).status_code == 200
    assert fleet.verify_token(spine, tok) is None


def test_device_activity_owner_only(spine, cfg):
    c, ho = _owner(spine, cfg)
    did = _dev(spine)
    fleet.enqueue(spine, did, "status")
    ha = _role(spine, cfg, c, "admin2", "admin")
    assert c.get(f"/uredjaji/{did}/aktivnost", headers=ha).status_code == 403  # admin ne smije
    r = c.get(f"/uredjaji/{did}/aktivnost", headers=ho)
    assert r.status_code == 200
    acts = r.json()["aktivnost"]
    assert acts and acts[0]["action"] == "status"


# --- agent poll/result (device token) --------------------------------------

def test_poll_requires_valid_token(spine, cfg):
    c, ho = _owner(spine, cfg)
    assert c.get("/agent/poll").status_code == 401
    assert c.get("/agent/poll", headers={"Authorization": "999.kriv"}).status_code == 401


def test_poll_returns_command_then_204(spine, cfg):
    c, ho = _owner(spine, cfg)
    did = _dev(spine)
    tok = c.post(f"/uredjaji/{did}/token", headers=ho).json()["token"]
    fleet.enqueue(spine, did, "status")
    ht = {"Authorization": tok}
    r = c.get("/agent/poll", headers=ht)
    assert r.status_code == 200 and r.json()["action"] == "status"
    assert c.get("/agent/poll", headers=ht).status_code == 204  # nema više


def test_result_only_for_own_command(spine, cfg):
    c, ho = _owner(spine, cfg)
    d1, d2 = _dev(spine), devices.add_device(spine, "radna-stanica", "PC2", user="a",
                                             host="192.168.1.11")["id"]
    t1 = c.post(f"/uredjaji/{d1}/token", headers=ho).json()["token"]
    t2 = c.post(f"/uredjaji/{d2}/token", headers=ho).json()["token"]
    cid = fleet.enqueue(spine, d1, "status")
    fleet.next_command(spine, d1)
    # d2 ne smije zatvoriti naredbu uređaja d1
    r = c.post("/agent/result", headers={"Authorization": t2},
               json={"id": cid, "ok": True, "detail": "x"})
    assert r.status_code in (403, 404)
    r = c.post("/agent/result", headers={"Authorization": t1},
               json={"id": cid, "ok": True, "detail": "gotovo"})
    assert r.status_code == 200
    row = spine.read().execute("SELECT status FROM agent_commands WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "done"
    # ponovno zatvaranje već završene naredbe se odbija (nema prepisivanja)
    assert c.post("/agent/result", headers={"Authorization": t1},
                  json={"id": cid, "ok": False, "detail": "lažni"}).status_code == 404


def test_result_rejects_pending_not_polled(spine, cfg):
    c, ho = _owner(spine, cfg)
    did = _dev(spine)
    tok = c.post(f"/uredjaji/{did}/token", headers=ho).json()["token"]
    cid = fleet.enqueue(spine, did, "status")  # pending, NIJE pollan/in_progress
    r = c.post("/agent/result", headers={"Authorization": tok},
               json={"id": cid, "ok": True, "detail": "preskočio izvršenje"})
    assert r.status_code == 404  # ne može se zatvoriti bez polla


# --- enqueue naredbe (admin+) + programi (owner) ---------------------------

def test_enqueue_admin_only(spine, cfg):
    c, ho = _owner(spine, cfg)
    did = _dev(spine)
    fleet.add_program(spine, "preglednik", "Preglednik", user="g")
    hv = _role(spine, cfg, c, "viewer1", "viewer")
    assert c.post(f"/uredjaji/{did}/naredba", headers=hv,
                  json={"action": "status"}).status_code == 403
    ha = _role(spine, cfg, c, "admin1", "admin")
    r = c.post(f"/uredjaji/{did}/naredba", headers=ha,
               json={"action": "run_program", "program_key": "preglednik"})
    assert r.status_code == 200
    row = spine.read().execute(
        "SELECT 1 FROM audit_log WHERE action='agent_naredba'").fetchone()
    assert row is not None


def test_enqueue_rejects_bad_action(spine, cfg):
    c, ho = _owner(spine, cfg)
    did = _dev(spine)
    ha = _role(spine, cfg, c, "admin1", "admin")
    assert c.post(f"/uredjaji/{did}/naredba", headers=ha,
                  json={"action": "rm_rf"}).status_code == 400


def test_programs_crud_owner_only(spine, cfg):
    c, ho = _owner(spine, cfg)
    ha = _role(spine, cfg, c, "admin1", "admin")
    assert c.post("/fleet/programi", headers=ha,
                  json={"key": "x", "label": "X"}).status_code == 403
    assert c.post("/fleet/programi", headers=ho,
                  json={"key": "Preglednik Weba", "label": "Web"}).status_code == 200
    progs = c.get("/fleet/programi", headers=ha).json()  # čitanje admin OK
    assert any(p["key"] == "preglednik_weba" for p in progs)
    assert c.request("DELETE", "/fleet/programi/preglednik_weba", headers=ho).status_code == 200
