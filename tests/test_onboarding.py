import base64
import os

import pytest
from fastapi.testclient import TestClient

from ragspine.business import onboarding
from ragspine.rag import retrieval
from ragspine.web.api import create_app
from ragspine.web.deps import add_user

VALID_OIB = "10000000000"
BAD_OIB = "12345678901"


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


# --- create_client ---------------------------------------------------

def test_create_client_valid_inserts_row_and_creates_folder(spine, cfg):
    result = onboarding.create_client(spine, cfg, {"name": "Pekara Mlinar", "oib": VALID_OIB}, "ana")
    assert result["id"] > 0
    assert result["nas_folder"] == f"klijenti/{result['id']}_pekara-mlinar"
    assert os.path.isdir(result["folder_path"])

    row = spine.read().execute("SELECT * FROM clients WHERE id=?", (result["id"],)).fetchone()
    assert row["name"] == "Pekara Mlinar"
    assert row["oib"] == VALID_OIB
    assert row["nas_folder"] == result["nas_folder"]


def test_create_client_bad_oib_raises(spine, cfg):
    with pytest.raises(ValueError):
        onboarding.create_client(spine, cfg, {"name": "Test d.o.o.", "oib": BAD_OIB}, "ana")


def test_create_client_missing_name_raises(spine, cfg):
    with pytest.raises(ValueError):
        onboarding.create_client(spine, cfg, {"name": ""}, "ana")
    with pytest.raises(ValueError):
        onboarding.create_client(spine, cfg, {}, "ana")


def test_create_client_no_oib_is_optional(spine, cfg):
    result = onboarding.create_client(spine, cfg, {"name": "Bez OIB-a"}, "ana")
    assert result["id"] > 0


def test_create_client_uses_nas_root_when_set(spine, cfg, tmp_path):
    nas = tmp_path / "nas"
    nas.mkdir()
    cfg.nas_root = str(nas)
    result = onboarding.create_client(spine, cfg, {"name": "Klijent A"}, "ana")
    assert os.path.commonpath([result["folder_path"], str(nas)]) == os.path.realpath(str(nas))


# --- slug --------------------------------------------------------------

def test_slug_sanitizes_dots_and_spaces():
    assert onboarding._slug("Pekara Mlinar d.o.o.") == "pekara-mlinar-d-o-o"


def test_slug_strips_traversal_sequences():
    s = onboarding._slug("../../etc")
    assert "/" not in s
    assert ".." not in s


# --- path escape ---------------------------------------------------------

def test_client_root_traversal_name_stays_inside_root(spine, cfg):
    result = onboarding.create_client(spine, cfg, {"name": "../../etc"}, "ana")
    root = os.path.realpath(cfg.data_dir)
    resolved = os.path.realpath(result["folder_path"])
    assert os.path.commonpath([resolved, root]) == root
    assert os.path.isdir(resolved)


# --- add_document --------------------------------------------------------

def test_add_document_text_file_ingested_and_searchable(spine, cfg):
    client = onboarding.create_client(spine, cfg, {"name": "Klijent Tekst"}, "ana")
    result = onboarding.add_document(spine, cfg, client["id"], "napomena.txt",
                                      "Ugovor o poslovnoj suradnji s klijentom.".encode("utf-8"),
                                      owner="ana")
    assert os.path.exists(result["path"])
    assert result["doc_id"] is not None
    hits = retrieval.search(spine, "poslovnoj suradnji")
    assert any(h.doc_id == result["doc_id"] for h in hits)


def test_add_document_fake_binary_still_saved_doc_id_none(spine, cfg):
    client = onboarding.create_client(spine, cfg, {"name": "Klijent Binaran"}, "ana")
    result = onboarding.add_document(spine, cfg, client["id"], "ugovor.pdf",
                                      b"\x00\x01not a real pdf", owner="ana")
    assert os.path.exists(result["path"])
    with open(result["path"], "rb") as f:
        assert f.read() == b"\x00\x01not a real pdf"
    # not a real PDF -> extraction fails -> file kept, doc_id None
    assert result["doc_id"] is None


def test_add_document_extension_allowlist_rejects_exe(spine, cfg):
    client = onboarding.create_client(spine, cfg, {"name": "Klijent Exe"}, "ana")
    with pytest.raises(ValueError):
        onboarding.add_document(spine, cfg, client["id"], "malware.exe", b"x", owner="ana")


def test_add_document_path_stays_inside_client_folder(spine, cfg):
    client = onboarding.create_client(spine, cfg, {"name": "Klijent Path"}, "ana")
    result = onboarding.add_document(spine, cfg, client["id"], "../../evil.pdf",
                                      b"x", owner="ana")
    assert os.path.commonpath([result["path"], client["folder_path"]]) == client["folder_path"]


def test_add_document_unknown_client_raises(spine, cfg):
    with pytest.raises(ValueError):
        onboarding.add_document(spine, cfg, 999999, "a.pdf", b"x", owner="ana")


def test_list_documents_shows_files_and_ingested_flag(spine, cfg):
    client = onboarding.create_client(spine, cfg, {"name": "Klijent Lista"}, "ana")
    onboarding.add_document(spine, cfg, client["id"], "a.pdf", b"binary junk", owner="ana")
    docs = onboarding.list_documents(spine, cfg, client["id"])
    assert len(docs) == 1
    assert docs[0]["filename"] == "a.pdf"
    assert docs[0]["size"] == len(b"binary junk")
    assert "ingested" in docs[0]


# --- onboard convenience --------------------------------------------------

def test_onboard_creates_client_and_ingests_documents(spine, cfg):
    result = onboarding.onboard(
        spine, cfg, {"name": "Klijent Onboard", "oib": VALID_OIB},
        [("a.pdf", b"binary junk one"), ("b.pdf", b"binary junk two")],
        "ana",
    )
    assert result["client"]["id"] > 0
    assert len(result["documents"]) == 2
    assert result["ingested"] == 0  # neither is a real parseable PDF


# --- API -------------------------------------------------------------

def test_api_create_client(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.post("/clients", json={"name": "API Klijent", "oib": VALID_OIB}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] > 0
    assert "nas_folder" in body


def test_api_create_client_bad_oib_400(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.post("/clients", json={"name": "Bad", "oib": BAD_OIB}, headers=headers)
    assert r.status_code == 400


def test_api_create_client_missing_name_400(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.post("/clients", json={"name": ""}, headers=headers)
    assert r.status_code == 400


def test_api_upload_document_and_list(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.post("/clients", json={"name": "API Doc Klijent"}, headers=headers)
    client_id = r.json()["id"]

    b64 = base64.b64encode(b"binary junk").decode()
    r = c.post(f"/clients/{client_id}/document",
               json={"filename": "a.pdf", "data_base64": b64}, headers=headers)
    assert r.status_code == 200
    assert "path" in r.json()

    r = c.get(f"/clients/{client_id}/documents", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_api_list_and_get_client(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.post("/clients", json={"name": "Karton Klijent", "oib": VALID_OIB}, headers=headers)
    client_id = r.json()["id"]

    r = c.get("/clients", headers=headers)
    assert r.status_code == 200
    assert any(row["id"] == client_id for row in r.json())

    r = c.get(f"/clients/{client_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["name"] == "Karton Klijent"


def test_api_clients_require_auth(spine, cfg):
    c = _client(spine, cfg)
    assert c.get("/clients").status_code == 401
    assert c.post("/clients", json={"name": "x"}).status_code == 401
    b64 = base64.b64encode(b"x").decode()
    assert c.post("/clients/1/document", json={"filename": "a.pdf", "data_base64": b64}).status_code == 401
    assert c.get("/clients/1/documents").status_code == 401
