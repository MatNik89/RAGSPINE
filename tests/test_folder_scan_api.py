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


def test_scan_endpoint_creates_notification(spine, tmp_path):
    root = tmp_path / "share"; kl = root / "KLIJENTI"
    (kl / "PERIĆ PERO").mkdir(parents=True)
    cfg = _cfg_roots(tmp_path, [str(root)])
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine)
    h = {"Authorization": f"Bearer {tok}"}
    r = c.post(f"/folders/{fid}/scan", headers=h)
    assert r.status_code == 200 and r.json()["n_subdirs"] == 1
    notifs = c.get("/notifications.json", headers=h).json()
    assert any(n["kind"] == "folder_connected" for n in notifs)
    # idempotentno: drugi scan ne duplicira obavijest
    c.post(f"/folders/{fid}/scan", headers=h)
    notifs2 = c.get("/notifications.json", headers=h).json()
    assert sum(1 for n in notifs2 if n["kind"] == "folder_connected") == 1


def test_scan_unknown_folder_404(spine, tmp_path):
    cfg = _cfg_roots(tmp_path, [str(tmp_path)])
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine)
    r = c.post("/folders/999/scan", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404
