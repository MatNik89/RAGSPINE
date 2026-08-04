from fastapi.testclient import TestClient

from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    add_user(spine, "ana", "tajna")
    c = TestClient(create_app(spine, cfg))
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_doc_types_requires_auth(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    assert c.get("/doc-types").status_code in (401, 403)


def test_doc_types_seed_and_crud(spine, cfg):
    c, h = _client(spine, cfg)
    rows = c.get("/doc-types", headers=h).json()
    assert "osobna_iskaznica" in [r["key"] for r in rows]

    r = c.post("/doc-types", headers=h, json={
        "key": "Putovnica", "label": "Putovnica",
        "fields": [{"key": "broj", "kind": "text"},
                   {"key": "vrijedi_do", "label": "Vrijedi do", "kind": "date", "expiry": True}]})
    assert r.status_code == 200 and r.json()["key"] == "putovnica"
    rows = {x["key"]: x for x in c.get("/doc-types", headers=h).json()}
    assert rows["putovnica"]["fields"][1]["expiry"] is True

    r = c.post("/doc-types", headers=h, json={
        "key": "x", "fields": [{"key": "a", "kind": "text", "expiry": True}]})
    assert r.status_code == 400


def test_doc_types_export_download(spine, cfg):
    c, h = _client(spine, cfg)
    r = c.get("/doc-types/export", headers=h)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["version"] == 1
    assert "osobna_iskaznica" in [t["key"] for t in data["doc_types"]]


def test_ui_dok_tipovi_page(spine, cfg):
    c, h = _client(spine, cfg)
    r = c.get("/ui/dok-tipovi", headers=h)
    assert r.status_code == 200 and "Vrste dokumenata" in r.text


def test_doc_types_field_coercion_and_types(spine, cfg):
    # Codex nalaz: "expiry":"false" (string) se prije spremao kao True
    c, h = _client(spine, cfg)
    r = c.post("/doc-types", headers=h, json={
        "key": "vozacka", "fields": [
            {"key": "vrijedi_do", "kind": "date", "expiry": "false"}]})
    assert r.status_code == 200
    t = {x["key"]: x for x in c.get("/doc-types", headers=h).json()}["vozacka"]
    assert t["fields"][0]["expiry"] is False
    # numeric key -> validacijska greška, ne 500
    r = c.post("/doc-types", headers=h, json={
        "key": "y", "fields": [{"key": 7, "kind": "text"}]})
    assert r.status_code in (400, 422)


def test_extract_endpoint(spine, cfg):
    from ragspine.docs.ingest import ingest_text
    c, h = _client(spine, cfg)
    doc_id = ingest_text(spine, "Broj: 12345\nDatum isteka: 01.02.2027\n" * 10,
                         title="osobna.pdf")
    r = c.post("/extract", headers=h,
               json={"doc_id": doc_id, "doc_type": "osobna_iskaznica"})
    assert r.status_code == 200
    data = r.json()
    assert data["fields"]["broj"] == "12345"
    assert data["fields"]["datum_isteka"] == "2027-02-01"
    r = c.post("/extract", headers=h, json={"doc_id": 9999, "doc_type": "osobna_iskaznica"})
    assert r.status_code == 400
