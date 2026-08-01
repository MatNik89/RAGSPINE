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
    ing.ingest_text(spine, "Podaci o klijentu koji posluje u Zagrebu.", "Firma X - dokument")


def test_forget_deletes_matching_rows(spine):
    _seed(spine)

    result = fg.forget(spine, "Firma X")

    assert result["clients"] > 0
    assert result["documents"] > 0
    assert result["notes"] > 0
    assert result["interactions"] > 0
    assert retrieval.search(spine, "Firma X") == []
    assert spine.read().execute("SELECT * FROM clients WHERE name='Firma X'").fetchone() is None
    assert spine.read().execute("SELECT * FROM chunks").fetchall() == []


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
    # the pre-write audit row for THIS request has detail=term, so it
    # self-matches the audit_log sweep — everything else stays at zero.
    result = fg.forget(spine, "Nepostojeca Tvrtka XYZ")
    assert result.pop("audit_log") == 1
    assert all(v == 0 for v in result.values())
