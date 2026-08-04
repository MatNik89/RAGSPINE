import hashlib

from ragspine.docs import forget as fg
from ragspine.docs import ingest as ing
from ragspine.rag import retrieval


def _seed(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("Firma X", "12345678901", "x@firma.hr", "091", "Ana"))
        c.execute("INSERT INTO notes(client_id,author,body) VALUES(?,?,?)",
                   (1, "Ana", "Firma X sastanak"))
        c.execute("INSERT INTO interactions(user,query,lane,answer) VALUES(?,?,?,?)",
                   ("Ana", "Firma X", "sql", "odgovor"))
        c.execute("INSERT INTO reminders(user,body,due) VALUES(?,?,?)",
                   ("Ana", "Firma X podsjetnik", "2026-08-01"))
    ing.ingest_text(spine, "Podaci o klijentu koji posluje u Zagrebu.", "Firma X - dokument")


def test_forget_deletes_matching_rows(spine):
    _seed(spine)

    result = fg.forget(spine, "Firma X")

    assert result["clients"] > 0
    assert result["documents"] > 0
    assert result["notes"] > 0
    assert result["interactions"] > 0
    assert result["reminders"] > 0
    assert retrieval.search(spine, "Firma X") == []
    assert spine.read().execute("SELECT * FROM clients WHERE name='Firma X'").fetchone() is None
    assert spine.read().execute("SELECT * FROM chunks").fetchall() == []
    assert spine.read().execute("SELECT * FROM reminders WHERE body LIKE '%Firma X%'").fetchone() is None


def test_forget_dry_run_deletes_nothing(spine):
    _seed(spine)

    result = fg.forget(spine, "Firma X", dry=True)

    assert result["clients"] > 0
    assert spine.read().execute("SELECT * FROM clients WHERE name='Firma X'").fetchone() is not None


def test_forget_rerun_dry_after_real_delete_is_all_zero(spine):
    _seed(spine)
    fg.forget(spine, "Firma X")

    result = fg.forget(spine, "Firma X", dry=True)

    assert all(v == 0 for v in result.values())


def test_forget_no_match_returns_all_zero(spine):
    # the pre-write audit row for THIS request is redacted (hash, not the raw
    # term) and written AFTER the sweep, so it never self-matches — count
    # stays genuinely zero across the board, including audit_log.
    result = fg.forget(spine, "Nepostojeca Tvrtka XYZ")
    assert all(v == 0 for v in result.values())


def test_forget_writes_redacted_proof_row_that_survives(spine):
    _seed(spine)

    fg.forget(spine, "Firma X")

    row = spine.read().execute(
        "SELECT * FROM audit_log WHERE action='gdpr_forget' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert "Firma X" not in row["detail"]
    digest = hashlib.sha256(b"Firma X").hexdigest()[:16]
    assert digest in row["detail"]

    # the proof row itself must not reintroduce a match on a re-sweep
    result = fg.forget(spine, "Firma X", dry=True)
    assert all(v == 0 for v in result.values())


def test_forget_escapes_like_metacharacters(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("AxB Ltd", "99999999999", "", "", ""))
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("A_B Ltd", "88888888888", "", "", ""))

    result = fg.forget(spine, "A_B")

    assert result["clients"] == 1
    assert spine.read().execute("SELECT * FROM clients WHERE name='AxB Ltd'").fetchone() is not None
    assert spine.read().execute("SELECT * FROM clients WHERE name='A_B Ltd'").fetchone() is None


# --- completeness: nove tablice + fajlovi (P0-gdpr fold) ---

def test_forget_sweeps_conversation_memory_and_cache(spine):
    with spine.write() as c:
        c.execute("INSERT INTO mem_l0(org_id,user_id,session_id,role,content) VALUES(1,1,'s','user',?)",
                  ("Ivan Horvat traži savjet",))
        c.execute("INSERT INTO mem_l1(org_id,user_id,kind,content) VALUES(1,1,'fact',?)",
                  ("Ivan Horvat je klijent",))
        c.execute("INSERT INTO query_cache(qhash,query,answer,meta) VALUES('h',?,?,'{}')",
                  ("tko je Ivan Horvat", "Ivan Horvat je..."))
        c.execute("INSERT INTO message_log(client_id,channel,status,subject,body_preview) "
                  "VALUES(1,'mail','sent',?,?)", ("Za Ivan Horvat", "pozdrav"))
    res = spine and __import__("ragspine.docs.forget", fromlist=["forget"]).forget(spine, "Ivan Horvat")
    assert res["mem_l0"] == 1 and res["mem_l1"] == 1
    assert res["query_cache"] == 1 and res["message_log"] == 1
    for t in ("mem_l0", "mem_l1", "query_cache", "message_log"):
        assert spine.read().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


def test_forget_unlinks_scanned_file_under_root(spine, cfg, tmp_path):
    from ragspine.docs.forget import forget
    root = tmp_path / "nas"
    root.mkdir()
    cfg.nas_root = str(root)
    f = root / "osobna-Ivan.pdf"
    f.write_bytes(b"%PDF fake")
    with spine.write() as c:
        c.execute("INSERT INTO documents(title,path,doc_type) VALUES(?,?,?)",
                  ("osobna Ivan", str(f), "osobna"))
    # dry ne briše ALI točno predviđa (Codex: prije je dry uvijek vraćao 0)
    dryres = forget(spine, "Ivan", dry=True, cfg=cfg)
    assert dryres.get("files") == 1
    assert f.exists()
    res = forget(spine, "Ivan", cfg=cfg)
    assert res.get("files") == 1
    assert not f.exists()


def test_forget_keeps_file_shared_by_survivor_via_path_alias(spine, cfg, tmp_path):
    from ragspine.docs.forget import forget
    root = tmp_path / "nas"; (root / "sub").mkdir(parents=True)
    cfg.nas_root = str(root)
    shared = root / "sub" / "doc.pdf"; shared.write_bytes(b"x")
    with spine.write() as c:
        c.execute("INSERT INTO documents(title,path,doc_type) VALUES(?,?,?)",
                  ("Ivan", str(shared), "d"))
        # preživjeli red drži ISTI fajl preko drugačijeg (alias) path stringa
        c.execute("INSERT INTO documents(title,path,doc_type) VALUES(?,?,?)",
                  ("Marko", str(root / "sub" / "." / "doc.pdf"), "d"))
    forget(spine, "Ivan", cfg=cfg)
    assert shared.exists()  # alias-canonicalizacija spasila preživjeli fajl


def test_forget_does_not_unlink_outside_root_or_symlink(spine, cfg, tmp_path):
    from ragspine.docs.forget import forget
    root = tmp_path / "nas"; root.mkdir()
    outside = tmp_path / "secret.pdf"; outside.write_bytes(b"x")
    cfg.nas_root = str(root)
    # symlink unutar roota koji cilja izvan → ne smije obrisati metu
    link = root / "Ivan-link.pdf"
    link.symlink_to(outside)
    with spine.write() as c:
        c.execute("INSERT INTO documents(title,path,doc_type) VALUES(?,?,?)",
                  ("Ivan", str(link), "osobna"))
    forget(spine, "Ivan", cfg=cfg)
    assert outside.exists()  # symlink meta netaknuta


def test_forget_keeps_file_shared_by_surviving_row(spine, cfg, tmp_path):
    from ragspine.docs.forget import forget
    root = tmp_path / "nas"; root.mkdir()
    cfg.nas_root = str(root)
    shared = root / "shared.pdf"; shared.write_bytes(b"x")
    with spine.write() as c:
        c.execute("INSERT INTO documents(title,path,doc_type) VALUES(?,?,?)", ("Ivan", str(shared), "d"))
        c.execute("INSERT INTO documents(title,path,doc_type) VALUES(?,?,?)", ("Marko", str(shared), "d"))
    forget(spine, "Ivan", cfg=cfg)  # briše Ivanov red, ali Markov drži isti fajl
    assert shared.exists()
