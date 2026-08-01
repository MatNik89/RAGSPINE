from fastapi.testclient import TestClient
from ragspine.web.api import create_app
from ragspine.web.deps import add_user

def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))

def test_health_no_auth(spine, cfg):
    r = _client(spine, cfg).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

def test_login_and_protected(spine, cfg):
    add_user(spine, "ana", "tajna")
    c = _client(spine, cfg)
    assert c.get("/v1/models").status_code == 401
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.get("/v1/models", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["object"] == "list"

def test_bad_login(spine, cfg):
    add_user(spine, "ana", "tajna")
    assert _client(spine, cfg).post("/auth/login",
        json={"username": "ana", "password": "x"}).status_code == 401

def test_malformed_login_body_400(spine, cfg):
    c = _client(spine, cfg)
    r = c.post("/auth/login", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 400

def test_empty_login_body_400(spine, cfg):
    c = _client(spine, cfg)
    assert c.post("/auth/login", json={}).status_code == 400
