import os

from fastapi.testclient import TestClient

from atlas.business import folders
from atlas.config import Config
from atlas.web.api import create_app
from atlas.web.deps import add_user


def _cfg_roots(tmp_path, roots):
    old = dict(os.environ)
    os.environ.update({"ATLAS_DATA_DIR": str(tmp_path / "data"),
                       "ATLAS_MOUNT_ROOTS": ",".join(roots)})
    try:
        return Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)


def _tok(c, spine):
    add_user(spine, "ana", "pw")
    return c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]


def test_discover_and_commit_endpoints(spine, tmp_path):
    root = tmp_path / "share"; kl = root / "KLIJENTI"
    (kl / "PERIĆ PERO").mkdir(parents=True)
    cfg = _cfg_roots(tmp_path, [str(root)])
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine)
    h = {"Authorization": f"Bearer {tok}"}
    cand = c.get(f"/clients/discover?folder_id={fid}", headers=h).json()
    assert cand[0]["raw_name"] == "PERIĆ PERO"
    r = c.post("/clients/discover/commit", headers=h, json={
        "folder_id": fid,
        "items": [{"subdir": "PERIĆ PERO", "name": "Perić Pero", "action": "import"}]})
    assert r.json()["created"] == 1


def test_uvoz_page_renders(spine, cfg):
    from tests.conftest import complete_setup
    add_user(spine, "ana", "pw")
    complete_setup(spine)
    c = TestClient(create_app(spine, cfg))
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    c.cookies.set("atlas_token", tok)
    r = c.get("/ui/klijenti-uvoz")
    assert r.status_code == 200 and "Uvoz klijenata" in r.text


def test_discover_requires_auth(spine, cfg):
    assert TestClient(create_app(spine, cfg)).get("/clients/discover?folder_id=1").status_code == 401
