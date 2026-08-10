"""Samo-prijava agenta: owner otvori sparivanje -> agent se javi -> owner odobri
-> agent preuzme token+sign_key. Nema ručnog upisivanja; ništa se ne izda dok
owner ne otvori prozor i ne klikne Odobri."""
import pytest
from fastapi.testclient import TestClient

from atlas.business import fleet, tenancy
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _owner(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


# --- business flow --------------------------------------------------------

def test_enroll_flow_business(spine, cfg):
    fleet.open_enrollment(spine, "g")
    eid, secret = fleet.enroll_request(spine, "PC-Ana")
    assert fleet.poll_enrollment(spine, cfg, eid, secret) == {"status": "pending"}
    assert [e["device_name"] for e in fleet.list_pending_enrollments(spine)] == ["PC-Ana"]
    did = fleet.approve_enrollment(spine, cfg, eid, None, "gazda")
    creds = fleet.poll_enrollment(spine, cfg, eid, secret)
    assert creds["status"] == "approved" and creds["device_id"] == did
    assert fleet.verify_token(spine, creds["token"]) == did  # token radi
    assert creds["sign_key"] == fleet.device_sign_key(spine, did, cfg)  # ispravan sign_key
    # jednokratno: drugi poll više ne daje kredencijale
    with pytest.raises(ValueError):
        fleet.poll_enrollment(spine, cfg, eid, secret)


def test_wrong_secret_rejected(spine, cfg):
    fleet.open_enrollment(spine, "g")
    eid, secret = fleet.enroll_request(spine, "X")
    with pytest.raises(ValueError):
        fleet.poll_enrollment(spine, cfg, eid, "krivo")


def test_creds_encrypted_at_rest(spine, cfg):
    fleet.open_enrollment(spine, "g")
    eid, secret = fleet.enroll_request(spine, "X")
    fleet.approve_enrollment(spine, cfg, eid, None, "g")
    row = spine.read().execute(
        "SELECT token_enc, signkey_enc FROM agent_enrollments WHERE id=?", (eid,)).fetchone()
    assert row["token_enc"].startswith("enc:") and row["signkey_enc"].startswith("enc:")


# --- sparivanje (pairing window) — fail-closed po defaultu -----------------

def test_enroll_closed_by_default(spine, cfg):
    # bez otvorenog prozora javni upis je zatvoren (anti-DoS/anti-impersonacija)
    with pytest.raises(ValueError):
        fleet.enroll_request(spine, "X")


def test_enroll_endpoint_403_when_closed(spine, cfg):
    c, _ = _owner(spine, cfg)
    r = c.post("/agent/enroll", json={})
    assert r.status_code == 403  # fail-closed


def test_open_then_enroll(spine, cfg):
    c, ho = _owner(spine, cfg)
    assert c.post("/uredjaji/enrollments/open", headers=ho).status_code == 200
    assert c.post("/agent/enroll", json={"name": "PC-X"}).status_code == 200


def test_open_requires_owner(spine, cfg):
    c, ho = _owner(spine, cfg)
    add_user(spine, "admin1", "pw")
    ta = c.post("/auth/login", json={"username": "admin1", "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, "admin")
    assert c.post("/uredjaji/enrollments/open",
                  headers={"Authorization": f"Bearer {ta}"}).status_code == 403


# --- endpoints ------------------------------------------------------------

def test_endpoint_full_flow(spine, cfg):
    c, ho = _owner(spine, cfg)
    c.post("/uredjaji/enrollments/open", headers=ho)
    r = c.post("/agent/enroll", json={"name": "PC-Boris"})
    assert r.status_code == 200
    eid, secret = r.json()["enroll_id"], r.json()["secret"]
    # prije odobrenja -> pending, bez kredencijala
    p = c.get(f"/agent/enroll/{eid}", headers={"X-Enroll-Secret": secret})
    assert p.json()["status"] == "pending"
    # owner vidi i odobri
    pend = c.get("/uredjaji/enrollments", headers=ho).json()
    assert any(e["id"] == eid for e in pend)
    a = c.post(f"/uredjaji/enrollments/{eid}/approve", headers=ho, json={})
    assert a.status_code == 200
    # agent preuzme
    got = c.get(f"/agent/enroll/{eid}", headers={"X-Enroll-Secret": secret}).json()
    assert got["status"] == "approved" and got["token"] and got["sign_key"]


def test_enrollments_list_owner_only(spine, cfg):
    c, ho = _owner(spine, cfg)
    add_user(spine, "admin1", "pw")
    ta = c.post("/auth/login", json={"username": "admin1", "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, "admin")
    assert c.get("/uredjaji/enrollments",
                 headers={"Authorization": f"Bearer {ta}"}).status_code == 403


def test_enroll_rate_limited(spine, cfg):
    c, ho = _owner(spine, cfg)
    c.post("/uredjaji/enrollments/open", headers=ho)
    codes = [c.post("/agent/enroll", json={}).status_code for _ in range(12)]
    assert 429 in codes  # anti-flood


def test_enroll_global_pending_cap(spine, cfg):
    # Codex: čak i s otvorenim prozorom, red na čekanju je omeđen
    fleet.open_enrollment(spine, "g")
    for _ in range(fleet._ENROLL_MAX_PENDING):
        fleet.enroll_request(spine, "x")
    with pytest.raises(ValueError):
        fleet.enroll_request(spine, "previše")


def test_per_source_cap(spine, cfg):
    # Codex #5: jedan IP ne smije sam ispuniti globalni red unutar prozora
    fleet.open_enrollment(spine, "g")
    for _ in range(fleet._ENROLL_MAX_PER_SRC):
        fleet.enroll_request(spine, "x", source="10.0.0.9")
    with pytest.raises(ValueError):
        fleet.enroll_request(spine, "x", source="10.0.0.9")
    # drugi IP i dalje prolazi -> nije globalno zaključan
    assert fleet.enroll_request(spine, "x", source="10.0.0.10")


def test_expired_pending_rejected_on_poll(spine, cfg):
    fleet.open_enrollment(spine, "g")
    eid, secret = fleet.enroll_request(spine, "x")
    with spine.write() as c:  # ostari zahtjev preko TTL-a
        c.execute("UPDATE agent_enrollments SET created_at=datetime('now','-1 hour') WHERE id=?", (eid,))
    with pytest.raises(ValueError):
        fleet.poll_enrollment(spine, cfg, eid, secret)


def test_expired_pending_rejected_on_approve(spine, cfg):
    # Codex: istekli pending ne smije se odobriti (inače siroti uređaj/token)
    fleet.open_enrollment(spine, "g")
    eid, _ = fleet.enroll_request(spine, "x")
    with spine.write() as c:
        c.execute("UPDATE agent_enrollments SET created_at=datetime('now','-1 hour') WHERE id=?", (eid,))
    with pytest.raises(ValueError):
        fleet.approve_enrollment(spine, cfg, eid, None, "g")


def test_double_approve_rejected(spine, cfg):
    # Codex TOCTOU: drugi approve istog reda -> odbijen, ne izdaje drugi token
    fleet.open_enrollment(spine, "g")
    eid, _ = fleet.enroll_request(spine, "x")
    fleet.approve_enrollment(spine, cfg, eid, None, "g")
    with pytest.raises(ValueError):
        fleet.approve_enrollment(spine, cfg, eid, None, "g")


def test_cleanup_keeps_approved_unclaimed(spine, cfg):
    # Codex: novi (istekli-pending) cleanup NE smije obrisati approved-neuzet red
    fleet.open_enrollment(spine, "g")
    eid, secret = fleet.enroll_request(spine, "x")
    fleet.approve_enrollment(spine, cfg, eid, None, "g")
    # ostari red i pokreni novi enroll (koji čisti) -> approved mora preživjeti
    with spine.write() as c:
        c.execute("UPDATE agent_enrollments SET created_at=datetime('now','-1 hour') WHERE id=?", (eid,))
    fleet.enroll_request(spine, "y")
    creds = fleet.poll_enrollment(spine, cfg, eid, secret)
    assert creds["status"] == "approved" and creds["token"]


def test_enroll_requires_https_when_https_only(spine, cfg):
    cfg.https_only = True
    try:
        c, ho = _owner(spine, cfg)
        c.post("/uredjaji/enrollments/open", headers=ho)
        r = c.post("/agent/enroll", json={})  # TestClient je http
        assert r.status_code == 400  # cleartext enroll odbijen
    finally:
        cfg.https_only = False


def test_forwarded_proto_not_trusted_without_proxy(spine, cfg):
    # Codex: bez trust_proxy, X-Forwarded-Proto: https ne smije zaobići https_only
    cfg.https_only = True
    try:
        c, ho = _owner(spine, cfg)
        c.post("/uredjaji/enrollments/open", headers=ho)
        r = c.post("/agent/enroll", json={}, headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 400  # lažni header ignoriran
    finally:
        cfg.https_only = False
