import pytest
from fastapi.testclient import TestClient

from ragspine.business import sop
from ragspine.rag import authority, retrieval
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _row(spine, sop_id):
    return spine.read().execute("SELECT * FROM sop_pages WHERE id=?", (sop_id,)).fetchone()


def test_new_sop_content_fills_template():
    content = sop.new_sop_content("Obrada ulaznih računa", "knjigovodstvo",
                                   procedure="1. Skeniraj\n2. Provjeri", tools="Pantheon",
                                   mistakes="Krivi konto", source="interni dogovor")
    assert "Obrada ulaznih računa" in content
    assert "knjigovodstvo" in content
    assert "Skeniraj" in content
    assert "Pantheon" in content
    assert "Krivi konto" in content
    assert "interni dogovor" in content


def test_create_sop_starts_as_draft(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kategorija", "sadržaj postupka")
    row = _row(spine, sop_id)
    assert row["status"] == "draft"
    assert row["author"] == "ana"


def test_submit_draft_moves_to_submitted(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj")
    sop.submit_draft(spine, sop_id, "ana")
    assert _row(spine, sop_id)["status"] == "submitted"


def test_submit_draft_requires_draft_status(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj")
    sop.submit_draft(spine, sop_id, "ana")
    with pytest.raises(ValueError):
        sop.submit_draft(spine, sop_id, "ana")  # already submitted


def test_approve_draft_requires_submitted_status(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj postupka o PDV-u")
    with pytest.raises(ValueError):
        sop.approve_draft(spine, sop_id, "iva")  # still draft, not submitted
    # no ingest happened
    hits = retrieval.search(spine, "sadržaj postupka")
    assert hits == []


def test_reject_draft_requires_submitted_status(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj")
    with pytest.raises(ValueError):
        sop.reject_draft(spine, sop_id, "iva", reason="nedovoljno detalja")


def test_approve_draft_ingests_and_is_searchable(spine):
    sop_id = sop.create_sop(spine, "ana", "Obrada e-računa za klijenta X", "knjigovodstvo",
                             "Koraci: preuzmi e-račun iz sustava i provjeri OIB dobavljača.")
    sop.submit_draft(spine, sop_id, "ana")
    doc_id = sop.approve_draft(spine, sop_id, "iva")

    assert doc_id is not None
    assert _row(spine, sop_id)["status"] == "approved"
    assert _row(spine, sop_id)["reviewer"] == "iva"

    doc = spine.read().execute("SELECT doc_type, title FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert doc["doc_type"] == "sop"
    assert doc["title"].startswith("SOP:")

    hits = retrieval.search(spine, "e-račun OIB dobavljača")
    assert any(h.doc_id == doc_id for h in hits)


def test_approved_sop_detected_as_interna_procedura(spine):
    sop_id = sop.create_sop(spine, "ana", "Postupak arhiviranja dokumenata", "arhiva",
                             "Arhiviraj dokumente po godini u NAS mapu.")
    sop.submit_draft(spine, sop_id, "ana")
    doc_id = sop.approve_draft(spine, sop_id, "iva")
    doc = spine.read().execute("SELECT title, doc_type FROM documents WHERE id=?", (doc_id,)).fetchone()
    tier, weight = authority.detect_authority(doc["title"], doc_type=doc["doc_type"])
    assert tier == "interna_procedura"


def test_reject_draft_moves_to_rejected(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj")
    sop.submit_draft(spine, sop_id, "ana")
    sop.reject_draft(spine, sop_id, "iva", reason="nedovoljno detalja")
    assert _row(spine, sop_id)["status"] == "rejected"


def test_list_pending_returns_only_submitted(spine):
    a = sop.create_sop(spine, "ana", "A", "kat", "sadržaj a")
    b = sop.create_sop(spine, "ana", "B", "kat", "sadržaj b")
    sop.create_sop(spine, "ana", "C draft", "kat", "sadržaj c")
    sop.submit_draft(spine, a, "ana")
    sop.submit_draft(spine, b, "ana")

    pending = sop.list_pending(spine)
    ids = {p["id"] for p in pending}
    assert ids == {a, b}


def test_editorial_summary_mentions_count(spine):
    a = sop.create_sop(spine, "ana", "A", "kat", "sadržaj a")
    sop.submit_draft(spine, a, "ana")
    text = sop.editorial_summary(spine)
    assert "1" in text
    assert "SOP" in text


def test_editorial_summary_empty_queue(spine):
    text = sop.editorial_summary(spine)
    assert "SOP" in text or "nema" in text.lower()


def test_get_sop_returns_dict_or_none(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj")
    row = sop.get_sop(spine, sop_id)
    assert row["title"] == "Naslov"
    assert sop.get_sop(spine, 999999) is None


def test_update_draft_bumps_version(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "v1 sadržaj")
    sop.update_draft(spine, sop_id, "ana", "v2 sadržaj")
    row = _row(spine, sop_id)
    assert row["content"] == "v2 sadržaj"
    assert row["base_version"] == 2


def test_update_draft_refuses_approved(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj")
    sop.submit_draft(spine, sop_id, "ana")
    sop.approve_draft(spine, sop_id, "iva")
    with pytest.raises(ValueError):
        sop.update_draft(spine, sop_id, "ana", "izmjena")


def test_update_draft_allowed_on_rejected(spine):
    sop_id = sop.create_sop(spine, "ana", "Naslov", "kat", "sadržaj")
    sop.submit_draft(spine, sop_id, "ana")
    sop.reject_draft(spine, sop_id, "iva", reason="loše")
    sop.update_draft(spine, sop_id, "ana", "popravljeno")
    assert _row(spine, sop_id)["content"] == "popravljeno"


def test_api_sop_roundtrip(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}

    r = c.post("/sop", json={"title": "API SOP", "category": "kat",
                              "content": "sadržaj postupka o obradi računa"}, headers=headers)
    assert r.status_code == 200
    sop_id = r.json()["id"]

    r = c.post(f"/sop/{sop_id}/submit", headers=headers)
    assert r.status_code == 200

    r = c.get("/sop/pending", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert any(item["id"] == sop_id for item in body["items"])
    assert "summary" in body

    r = c.post(f"/sop/{sop_id}/approve", headers=headers)
    assert r.status_code == 200
    doc_id = r.json()["doc_id"]
    assert doc_id is not None

    r = c.get(f"/sop/{sop_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


def test_api_sop_reject(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}

    r = c.post("/sop", json={"title": "SOP za odbiti", "category": "kat",
                              "content": "sadržaj"}, headers=headers)
    sop_id = r.json()["id"]
    c.post(f"/sop/{sop_id}/submit", headers=headers)

    r = c.post(f"/sop/{sop_id}/reject", json={"reason": "nepotpuno"}, headers=headers)
    assert r.status_code == 200

    r = c.get(f"/sop/{sop_id}", headers=headers)
    assert r.json()["status"] == "rejected"


def test_api_sop_requires_auth(spine, cfg):
    c = _client(spine, cfg)
    assert c.post("/sop", json={"title": "x", "category": "y", "content": "z"}).status_code == 401
