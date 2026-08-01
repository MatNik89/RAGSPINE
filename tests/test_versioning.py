import pytest
from fastapi.testclient import TestClient

from ragspine.docs import ingest as ing
from ragspine.rag import retrieval, versioning as ver
from ragspine.web import watchlist as w
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _status(spine, doc_id):
    return spine.read().execute("SELECT status FROM documents WHERE id=?", (doc_id,)).fetchone()["status"]


def test_set_status_updates_and_validates(spine):
    doc_id = ing.ingest_text(spine, "Neki tekst o PDV-u.", "doc1", doc_type="zakon")
    ver.set_status(spine, doc_id, "draft")
    assert _status(spine, doc_id) == "draft"
    with pytest.raises(ValueError):
        ver.set_status(spine, doc_id, "bogus")


def test_supersede_marks_old_and_activates_new(spine):
    v1 = ing.ingest_text(spine, "Zakon verzija 1.", "zakon-x", doc_type="zakon")
    v2 = ing.ingest_text(spine, "Zakon verzija 2.", "zakon-x-v2", doc_type="zakon")
    ver.supersede(spine, v1, v2)
    row1 = spine.read().execute("SELECT * FROM documents WHERE id=?", (v1,)).fetchone()
    row2 = spine.read().execute("SELECT * FROM documents WHERE id=?", (v2,)).fetchone()
    assert row1["status"] == "superseded"
    assert row2["status"] == "active"
    assert row2["supersedes"] == v1
    assert row2["version"] == 2
    # both rows still exist (audit)
    assert row1 is not None and row2 is not None


def test_retrieval_excludes_superseded_and_draft(spine):
    doc_id = ing.ingest_text(spine, "Stopa PDV-a iznosi 25 posto u Hrvatskoj.", "pdv-doc", doc_type="zakon")
    hits = retrieval.search(spine, "stopa PDV")
    assert any(h.title == "pdv-doc" for h in hits)

    ver.set_status(spine, doc_id, "superseded")
    hits = retrieval.search(spine, "stopa PDV")
    assert all(h.title != "pdv-doc" for h in hits)

    draft_id = ver.stage_draft(spine, "Nova nacrt-verzija o PDV-u dvadeset i pet posto.", "pdv-draft", doc_type="zakon")
    hits = retrieval.search(spine, "nacrt-verzija PDV")
    assert all(h.title != "pdv-draft" for h in hits)

    ver.promote_draft(spine, draft_id)
    hits = retrieval.search(spine, "nacrt-verzija PDV")
    assert any(h.title == "pdv-draft" for h in hits)


def test_promote_draft_requires_draft_status(spine):
    doc_id = ing.ingest_text(spine, "Neki tekst.", "doc2", doc_type="zakon")
    with pytest.raises(ValueError):
        ver.promote_draft(spine, doc_id)  # already active, not draft


def test_version_history_walks_chain(spine):
    v1 = ing.ingest_text(spine, "Verzija 1.", "v-chain-1", doc_type="zakon")
    v2 = ing.ingest_text(spine, "Verzija 2.", "v-chain-2", doc_type="zakon")
    v3 = ing.ingest_text(spine, "Verzija 3.", "v-chain-3", doc_type="zakon")
    ver.supersede(spine, v1, v2)
    ver.supersede(spine, v2, v3)

    hist = ver.version_history(spine, v3)
    versions = [h["version"] for h in hist]
    doc_ids = [h["doc_id"] for h in hist]
    assert versions == [1, 2, 3]
    assert doc_ids == [v1, v2, v3]

    # walking from the middle or the oldest should give the same chain
    hist_mid = ver.version_history(spine, v2)
    assert [h["doc_id"] for h in hist_mid] == [v1, v2, v3]


def test_legacy_null_status_treated_as_active(spine):
    doc_id = ing.ingest_text(spine, "Legacy dokument bez statusa.", "legacy-doc", doc_type="zakon")
    with spine.write() as c:
        c.execute("UPDATE documents SET status=NULL WHERE id=?", (doc_id,))
    hits = retrieval.search(spine, "legacy dokument bez statusa")
    assert any(h.title == "legacy-doc" for h in hits)


def test_active_version_returns_single_active(spine):
    v1 = ing.ingest_text(spine, "Verzija A.", "av-doc", doc_type="zakon", source_url="https://x/a")
    v2 = ing.ingest_text(spine, "Verzija B.", "av-doc-2", doc_type="zakon", source_url="https://x/a")
    ver.supersede(spine, v1, v2)
    active = ver.active_version(spine, "https://x/a")
    assert active is not None and active["doc_id"] == v2


HTML1 = b"<html><body>Prirez Zagreb iznosi 10%.</body></html>"
HTML2 = b"<html><body>Prirez Zagreb iznosi 18%.</body></html>"
HTML3 = b"<html><body>Prirez Zagreb iznosi 22%.</body></html>"


def test_watchlist_supersedes_old_version(spine, cfg):
    sid = w.add_source(spine, "https://porezna.example/versioning")
    w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML1)  # baseline, no ingest
    w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML2)  # change -> ingests v1
    old_doc = spine.read().execute(
        "SELECT id FROM documents WHERE source_url=?", ("https://porezna.example/versioning",)
    ).fetchone()["id"]

    w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML3)  # change -> ingests v2, supersedes v1

    rows = spine.read().execute(
        "SELECT id, status FROM documents WHERE source_url=? ORDER BY id", ("https://porezna.example/versioning",)
    ).fetchall()
    assert len(rows) == 2
    statuses = {r["id"]: r["status"] for r in rows}
    assert statuses[old_doc] == "superseded"
    new_doc = [r["id"] for r in rows if r["id"] != old_doc][0]
    assert statuses[new_doc] == "active"


def test_api_status_supersede_versions_promote_roundtrip(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    headers = {"Authorization": f"Bearer {tok}"}

    v1 = ing.ingest_text(spine, "API verzija 1.", "api-v1", doc_type="zakon")
    draft_id = ver.stage_draft(spine, "API draft content.", "api-draft", doc_type="zakon")

    r = c.post(f"/knowledge/{draft_id}/promote", headers=headers)
    assert r.status_code == 200
    assert _status(spine, draft_id) == "active"

    r = c.post(f"/knowledge/{v1}/status", json={"status": "draft"}, headers=headers)
    assert r.status_code == 200
    assert _status(spine, v1) == "draft"

    r = c.post(f"/knowledge/{v1}/promote", headers=headers)
    assert r.status_code == 200

    v2 = ing.ingest_text(spine, "API verzija 2.", "api-v2", doc_type="zakon")
    r = c.post("/knowledge/supersede", json={"old_doc_id": v1, "new_doc_id": v2}, headers=headers)
    assert r.status_code == 200
    assert _status(spine, v1) == "superseded"
    assert _status(spine, v2) == "active"

    r = c.get(f"/knowledge/{v2}/versions", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert [h["doc_id"] for h in body] == [v1, v2]
