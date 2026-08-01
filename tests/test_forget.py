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
