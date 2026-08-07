from fastapi.testclient import TestClient

from atlas.web.api import create_app
from atlas.web.deps import add_user


def _h(spine, cfg):
    add_user(spine, "ana", "pw")
    c = TestClient(create_app(spine, cfg))
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_folder_note_persists(spine, cfg):
    c, h = _h(spine, cfg)
    r = c.post("/notes/folder", json={"folder_id": 3, "body": "Perić ima dva obrta"}, headers=h)
    assert r.status_code == 200 and r.json()["key"] == "note:folder:3"
    row = spine.read().execute("SELECT value FROM memory WHERE key='note:folder:3'").fetchone()
    assert row["value"] == "Perić ima dva obrta"


def test_global_note_and_overwrite(spine, cfg):
    c, h = _h(spine, cfg)
    c.post("/notes/folder", json={"body": "prva"}, headers=h)
    c.post("/notes/folder", json={"body": "druga"}, headers=h)  # overwrite
    row = spine.read().execute("SELECT value FROM memory WHERE key='note:global'").fetchone()
    assert row["value"] == "druga"


def test_note_requires_auth(spine, cfg):
    assert TestClient(create_app(spine, cfg)).post(
        "/notes/folder", json={"body": "x"}).status_code == 401
