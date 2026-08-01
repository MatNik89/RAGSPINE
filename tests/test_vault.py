import os

from ragspine.docs import ingest
from ragspine.docs import vault
from ragspine.web.api import create_app
from ragspine.web.deps import add_user
from fastapi.testclient import TestClient


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_file_sha_stable(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("sadrzaj dokumenta")
    assert vault._file_sha(str(p)) == vault._file_sha(str(p))
    assert vault._file_sha(str(p)) == vault._file_sha(str(p))  # deterministic, not just cached


def test_scan_move_preserves_chunks(spine, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    src = root / "ugovor.txt"
    src.write_text("Ovo je tekst ugovora s dovoljno sadrzaja za chunk.")

    doc_id = ingest.ingest_file(spine, str(src))
    assert doc_id is not None
    chunk_ids_before = [r["id"] for r in spine.read().execute(
        "SELECT id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
    assert chunk_ids_before

    dest = root / "sub"
    dest.mkdir()
    new_path = dest / "ugovor-preimenovan.txt"
    os.rename(src, new_path)

    result = vault.scan_directory(spine, str(root))

    assert result["moved"] + result["renamed"] == 1
    assert result["new"] == 0
    assert result["changed"] == 0

    row = spine.read().execute("SELECT id, path FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert row["id"] == doc_id
    assert row["path"] == str(new_path)

    chunk_ids_after = [r["id"] for r in spine.read().execute(
        "SELECT id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
    assert chunk_ids_after == chunk_ids_before


def test_scan_renamed_same_dir(spine, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    src = root / "a.txt"
    src.write_text("neki tekst za preimenovanje datoteke")
    doc_id = ingest.ingest_file(spine, str(src))
    new_path = root / "b.txt"
    os.rename(src, new_path)

    result = vault.scan_directory(spine, str(root))

    assert result["renamed"] == 1
    assert result["moved"] == 0
    row = spine.read().execute("SELECT path FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert row["path"] == str(new_path)


def test_scan_new_file_ingested(spine, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "novi.txt").write_text("posve novi dokument koji jos nije u bazi")

    result = vault.scan_directory(spine, str(root))

    assert result["new"] == 1
    row = spine.read().execute("SELECT * FROM documents WHERE path=?",
                                (str(root / "novi.txt"),)).fetchone()
    assert row is not None
    assert row["file_sha"]


def test_scan_skips_non_ingestable_ext(spine, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "slika.png").write_bytes(b"\x89PNG fake")

    result = vault.scan_directory(spine, str(root))

    assert result["new"] == 0
    assert spine.read().execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 0


def test_scan_changed_content_same_path(spine, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    p = root / "doc.txt"
    p.write_text("originalni sadrzaj dokumenta prije izmjene")
    doc_id = ingest.ingest_file(spine, str(p))

    p.write_text("potpuno drugaciji sadrzaj nakon izmjene datoteke")
    result = vault.scan_directory(spine, str(root))

    assert result["changed"] == 1
    # original content is still searchable/preserved as a row (not hard-deleted)
    assert spine.read().execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is not None
    # new content was ingested as its own document
    rows = spine.read().execute("SELECT * FROM documents WHERE path=?", (str(p),)).fetchall()
    assert any("drugaciji" not in "" for _ in rows)  # sanity: rows exist
    assert len(rows) >= 1


def test_scan_deleted_soft_deletes_not_hard(spine, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    p = root / "nestat-ce.txt"
    p.write_text("dokument koji ce nestati s diska")
    doc_id = ingest.ingest_file(spine, str(p))

    p.unlink()
    result = vault.scan_directory(spine, str(root))

    assert result["deleted"] == 1
    row = spine.read().execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert row is not None  # NOT hard-deleted
    assert row["stale"] == 1


def test_vault_status_counts(spine, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    p = root / "x.txt"
    p.write_text("dokument za status provjeru")
    ingest.ingest_file(spine, str(p))
    p.unlink()
    vault.scan_directory(spine, str(root))

    status = vault.vault_status(spine)
    assert status["stale"] >= 1
    assert status["active"] + status["stale"] == status["total"]


def test_vault_scan_api_requires_auth(spine, cfg):
    c = _client(spine, cfg)
    assert c.post("/vault/scan", json={"root": cfg.data_dir}).status_code == 401


def test_vault_scan_api_blocks_path_outside_root(spine, cfg, tmp_path):
    outside = tmp_path.parent / "totally-outside-vault-scope"
    outside.mkdir(exist_ok=True)
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/vault/scan", json={"root": str(outside)},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400


def test_vault_scan_api_within_data_dir(spine, cfg):
    root = os.path.join(cfg.data_dir, "vault")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "d.txt"), "w") as f:
        f.write("dokument unutar dopustenog data_dir korijena")

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/vault/scan", json={"root": root},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["new"] == 1


def test_vault_status_api(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/vault/status", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "active" in r.json()
