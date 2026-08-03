"""Faza A spojnog tkiva: org-kontekst u auth (JWT claims, require_actor, /org)."""
from fastapi.testclient import TestClient

from ragspine.business import tenancy
from ragspine.core.security import jwt_decode, jwt_encode
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _login(c, spine, username="ana", password="tajna", role="radnik"):
    add_user(spine, username, password, role)
    return c.post("/auth/login", json={"username": username, "password": password}).json()["token"]


def test_login_token_has_org_claims(spine, cfg):
    c = _client(spine, cfg)
    tok = _login(c, spine)
    payload = jwt_decode(tok, cfg.jwt_secret)
    assert payload["uid"] and payload["org_id"]


def test_first_login_bootstraps_default_org_owner(spine, cfg):
    c = _client(spine, cfg)
    tok = _login(c, spine)
    payload = jwt_decode(tok, cfg.jwt_secret)
    assert tenancy.role_of(spine, payload["org_id"], payload["uid"]) == "owner"


def test_second_login_is_member_admin_maps_admin(spine, cfg):
    c = _client(spine, cfg)
    _login(c, spine, "ana", "tajna")
    tok2 = _login(c, spine, "boris", "tajna2")
    p2 = jwt_decode(tok2, cfg.jwt_secret)
    assert tenancy.role_of(spine, p2["org_id"], p2["uid"]) == "member"
    tok3 = _login(c, spine, "cvita", "tajna3", role="admin")
    p3 = jwt_decode(tok3, cfg.jwt_secret)
    assert tenancy.role_of(spine, p3["org_id"], p3["uid"]) == "admin"


def test_org_endpoint_returns_members_and_fresh_role(spine, cfg):
    c = _client(spine, cfg)
    tok = _login(c, spine)
    r = c.get("/org", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    d = r.json()
    assert d["role"] == "owner" and d["members"][0]["username"] == "ana"
    # promjena uloge vrijedi ODMAH (uloga se ne čita iz tokena)
    _login(c, spine, "boris", "tajna2")
    payload = jwt_decode(tok, cfg.jwt_secret)
    tenancy.add_member(spine, payload["org_id"], payload["uid"], "viewer")
    assert c.get("/org", headers={"Authorization": f"Bearer {tok}"}).json()["role"] == "viewer"


def test_old_token_without_claims_still_resolves(spine, cfg):
    c = _client(spine, cfg)
    _login(c, spine)  # kreira usera + membership
    old = jwt_encode({"sub": "ana", "role": "radnik"}, cfg.jwt_secret)
    r = c.get("/org", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 200 and r.json()["role"] == "owner"


def test_org_requires_auth(spine, cfg):
    assert _client(spine, cfg).get("/org").status_code == 401


def test_old_token_without_membership_forces_relogin(spine, cfg):
    """Fallback je read-only: NE stvara membership na GET zahtjevu."""
    c = _client(spine, cfg)
    add_user(spine, "novi", "pw")  # račun postoji, nikad se nije logirao
    old = jwt_encode({"sub": "novi", "role": "radnik"}, cfg.jwt_secret)
    assert c.get("/org", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert spine.read().execute("SELECT COUNT(*) AS n FROM memberships").fetchone()["n"] == 0


def test_unknown_user_in_old_token_401(spine, cfg):
    c = _client(spine, cfg)
    ghost = jwt_encode({"sub": "duh", "role": "radnik"}, cfg.jwt_secret)
    assert c.get("/org", headers={"Authorization": f"Bearer {ghost}"}).status_code == 401
