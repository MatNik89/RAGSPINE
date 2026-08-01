from ragspine.business import checklist


def _client(spine, **fields):
    cols = ", ".join(fields.keys())
    qs = ", ".join("?" for _ in fields)
    with spine.write() as c:
        cur = c.execute(f"INSERT INTO clients({cols}) VALUES({qs})", tuple(fields.values()))
        return cur.lastrowid


def _doc(spine, client_id, doc_type):
    with spine.write() as c:
        c.execute("INSERT INTO documents(client_id, doc_type) VALUES(?,?)", (client_id, doc_type))


def test_score_client_only_name(spine):
    cid = _client(spine, name="Alfa")
    result = checklist.score_client(spine, cid)
    assert result["score"] < 50
    assert "OIB" in result["missing"]
    assert result["client"] == "Alfa"


def test_score_client_complete(spine):
    cid = _client(spine, name="Beta", oib="123", email="a@b.hr", phone="099",
                   owner="Ana", industry="IT", pdv_status="u sustavu pdv")
    _doc(spine, cid, "ugovor")
    _doc(spine, cid, "izvod")
    result = checklist.score_client(spine, cid)
    assert result["score"] == 100
    assert result["missing"] == []


def test_worst_first_orders_ascending(spine):
    good = _client(spine, name="Beta", oib="1", email="a@b.hr", phone="099",
                    owner="Ana", industry="IT", pdv_status="u sustavu pdv")
    _doc(spine, good, "ugovor")
    _doc(spine, good, "izvod")
    bad = _client(spine, name="Alfa")
    rows = checklist.worst_first(spine)
    assert [r["client"] for r in rows[:2]] == ["Alfa", "Beta"]
    assert bad and good  # keep linters quiet re unused
