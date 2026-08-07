import os

import pytest
from fastapi.testclient import TestClient

from atlas.business import folders
from atlas.config import Config
from atlas.docs import ocr
from atlas.web.api import create_app
from atlas.web.deps import add_user


def _cfg(tmp_path, share):
    old = dict(os.environ)
    os.environ.update({"ATLAS_DATA_DIR": str(tmp_path / "d"),
                       "ATLAS_MOUNT_ROOTS": str(share)})
    try:
        return Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)


def _tok(c, spine):
    add_user(spine, "ana", "pw")
    return c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]


def test_folder_ocr_endpoint(spine, tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    share = tmp_path / "KLIJENTI"; share.mkdir()
    d = fitz.open(); d.new_page(); d.save(str(share / "skan.pdf")); d.close()
    cfg = _cfg(tmp_path, share)
    fid = folders.register(spine, cfg, str(share), "klijenti")["id"]
    long_text = "tekst dovoljne duljine za test. " * 6
    monkeypatch.setattr(ocr, "ocr_page_best", lambda png, c, transport=None: (long_text, "tesseract"))
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine)
    h = {"Authorization": f"Bearer {tok}"}
    r = c.post(f"/folders/{fid}/ocr", headers=h)
    assert r.status_code == 200 and r.json()["processed"] == 1
    notifs = c.get("/notifications.json", headers=h).json()
    assert any(n["kind"] == "folder_ocred" for n in notifs)
    a = c.get(f"/folders/{fid}/ocr/audit", headers=h).json()
    assert "n_pdf_no_text" in a


def test_folder_ocr_unknown_404(spine, tmp_path):
    cfg = _cfg(tmp_path, tmp_path)
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine)
    assert c.post("/folders/999/ocr", headers={"Authorization": f"Bearer {tok}"}).status_code == 404
