import base64
import os

import pytest
from fastapi.testclient import TestClient

from ragspine.business import sop, sop_images
from ragspine.rag import retrieval
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _sop(spine):
    return sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj postupka")


def fake_transport_ok(url, headers, body):
    return {"choices": [{"message": {"content": "Tekst sa slike"}}]}


def fake_transport_fail(url, headers, body):
    raise RuntimeError("OCR server down")


def test_add_image_stores_file_and_ocr_text(spine, cfg):
    sop_id = _sop(spine)
    result = sop_images.add_image(spine, cfg, sop_id, "screenshot.png", b"fakepngbytes",
                                   transport=fake_transport_ok)
    assert result["ocr_text"] == "Tekst sa slike"
    assert os.path.exists(result["path"])
    assert os.path.dirname(result["path"]) == sop_images._images_dir(cfg)
    with open(result["path"], "rb") as f:
        assert f.read() == b"fakepngbytes"


def test_add_image_second_image_gets_unique_path(spine, cfg):
    sop_id = _sop(spine)
    r1 = sop_images.add_image(spine, cfg, sop_id, "shot.png", b"one", transport=fake_transport_ok)
    r2 = sop_images.add_image(spine, cfg, sop_id, "shot.png", b"two", transport=fake_transport_ok)
    assert r1["path"] != r2["path"]
    with open(r1["path"], "rb") as f:
        assert f.read() == b"one"
    with open(r2["path"], "rb") as f:
        assert f.read() == b"two"


def test_add_image_requires_existing_sop(spine, cfg):
    with pytest.raises(ValueError):
        sop_images.add_image(spine, cfg, 999999, "a.png", b"x", transport=fake_transport_ok)


def test_filename_path_traversal_sanitized(spine, cfg):
    sop_id = _sop(spine)
    result = sop_images.add_image(spine, cfg, sop_id, "../../evil.png", b"x", transport=fake_transport_ok)
    images_dir = os.path.realpath(sop_images._images_dir(cfg))
    resolved = os.path.realpath(result["path"])
    assert os.path.commonpath([resolved, images_dir]) == images_dir


def test_disallowed_extension_rejected(spine, cfg):
    sop_id = _sop(spine)
    with pytest.raises(ValueError):
        sop_images.add_image(spine, cfg, sop_id, "malware.exe", b"x", transport=fake_transport_ok)


def test_ocr_failure_still_stores_image(spine, cfg):
    sop_id = _sop(spine)
    result = sop_images.add_image(spine, cfg, sop_id, "shot.png", b"data", transport=fake_transport_fail)
    assert result["ocr_text"] == ""
    assert os.path.exists(result["path"])


def test_image_bytes_returns_stored_data(spine, cfg):
    sop_id = _sop(spine)
    result = sop_images.add_image(spine, cfg, sop_id, "shot.png", b"imagedata", transport=fake_transport_ok)
    data, mime = sop_images.image_bytes(spine, cfg, result["id"])
    assert data == b"imagedata"
    assert mime.startswith("image/")


def test_image_bytes_missing_returns_none(spine, cfg):
    assert sop_images.image_bytes(spine, cfg, 999999) is None


def test_image_bytes_path_escape_guard_refused(spine, cfg):
    sop_id = _sop(spine)
    result = sop_images.add_image(spine, cfg, sop_id, "shot.png", b"data", transport=fake_transport_ok)
    with spine.write() as c:
        c.execute("UPDATE sop_images SET path=? WHERE id=?", ("/etc/passwd", result["id"]))
    assert sop_images.image_bytes(spine, cfg, result["id"]) is None


def test_list_images_returns_metadata(spine, cfg):
    sop_id = _sop(spine)
    sop_images.add_image(spine, cfg, sop_id, "a.png", b"1", caption="prvi", transport=fake_transport_ok)
    sop_images.add_image(spine, cfg, sop_id, "b.png", b"2", caption="drugi", transport=fake_transport_ok)
    images = sop_images.list_images(spine, sop_id)
    assert len(images) == 2
    assert {i["caption"] for i in images} == {"prvi", "drugi"}
    assert all("ocr_text" in i for i in images)


def test_approve_flow_makes_image_ocr_searchable(spine, cfg):
    sop_id = sop.create_sop(spine, "ana", "Upute za plaće", "kat", "Postupak obrade plaća.")
    sop_images.add_image(
        spine, cfg, sop_id, "shot.png", b"screenshot-bytes",
        transport=lambda u, h, b: {"choices": [{"message": {"content": "kliknite gumb Plaće"}}]},
    )
    sop.submit_draft(spine, sop_id, "ana")
    doc_id = sop.approve_draft(spine, sop_id, "iva")

    assert doc_id is not None
    hits = retrieval.search(spine, "kliknite gumb Plaće")
    assert any(h.doc_id == doc_id for h in hits)


def test_api_image_upload_list_and_serve(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}

    r = c.post("/sop", json={"title": "API SOP slike", "category": "kat",
                              "content": "sadržaj postupka"}, headers=headers)
    sop_id = r.json()["id"]

    b64 = base64.b64encode(b"\x89PNGfakebytes").decode()
    r = c.post(f"/sop/{sop_id}/image",
               json={"filename": "screenshot.png", "data_base64": b64, "caption": "korak 1"},
               headers=headers)
    assert r.status_code == 200
    assert "ocr_text_len" in r.json()
    image_id = r.json()["id"]

    r = c.get(f"/sop/{sop_id}/images", headers=headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["caption"] == "korak 1"

    r = c.get(f"/sop/image/{image_id}", headers=headers)
    assert r.status_code == 200
    assert r.content == b"\x89PNGfakebytes"
    assert r.headers["content-type"].startswith("image/")


def test_api_image_missing_returns_404(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.get("/sop/image/999999", headers=headers)
    assert r.status_code == 404


def test_api_image_endpoints_require_auth(spine, cfg):
    c = _client(spine, cfg)
    assert c.get("/sop/1/images").status_code == 401
    assert c.get("/sop/image/1").status_code == 401
    b64 = base64.b64encode(b"x").decode()
    assert c.post("/sop/1/image", json={"filename": "a.png", "data_base64": b64}).status_code == 401
