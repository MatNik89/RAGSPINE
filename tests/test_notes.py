from ragspine.business import auditlog, notes


def _client(spine, name="Alfa", oib="1"):
    with spine.write() as c:
        cur = c.execute("INSERT INTO clients(name, oib) VALUES(?,?)", (name, oib))
        return cur.lastrowid


def test_add_and_search_by_term(spine):
    cid = _client(spine)
    note_id = notes.add(spine, cid, "ana", "poziv klijenta oko PDV-a")
    assert isinstance(note_id, int)

    found = notes.search(spine, term="PDV")
    assert any(r["id"] == note_id and r["name"] == "Alfa" for r in found)

    empty = notes.search(spine, term="nepostojeci-pojam-xyz")
    assert all(r["id"] != note_id for r in empty)


def test_search_filters_by_client_id(spine):
    cid1 = _client(spine, "Alfa", "1")
    cid2 = _client(spine, "Beta", "2")
    notes.add(spine, cid1, "ana", "bilješka za Alfu")
    notes.add(spine, cid2, "ana", "bilješka za Betu")

    rows = notes.search(spine, client_id=cid1)
    assert len(rows) == 1
    assert rows[0]["client_id"] == cid1


def test_add_writes_audit_log(spine):
    cid = _client(spine)
    notes.add(spine, cid, "ana", "test bilješka")
    rows = auditlog.search(spine, action="note_add")
    assert any(r["user"] == "ana" and r["action"] == "note_add" for r in rows)
