from fastapi.testclient import TestClient
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_memory_write_and_get_roundtrip(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}

    r = c.post("/memory", json={"key": "boja", "value": "plava"}, headers=headers)
    assert r.status_code == 200

    r = c.get("/memory/boja", headers=headers)
    assert r.status_code == 200
    assert r.json()["value"] == "plava"


def test_memory_get_missing_key_404(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/memory/nope", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


def test_memory_hot_lists_sorted(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}
    c.post("/memory", json={"key": "a", "value": "1"}, headers=headers)
    c.post("/memory", json={"key": "b", "value": "2"}, headers=headers)
    c.get("/memory/b", headers=headers)

    r = c.get("/memory/hot", headers=headers)
    assert r.status_code == 200
    keys = [row["key"] for row in r.json()]
    assert keys[0] == "b"


def test_memory_requires_auth(spine, cfg):
    c = _client(spine, cfg)
    assert c.get("/memory/hot").status_code == 401
    assert c.post("/memory", json={"key": "k", "value": "v"}).status_code == 401
