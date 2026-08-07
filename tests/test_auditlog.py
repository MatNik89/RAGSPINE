from atlas.business import auditlog


def test_search_filters_by_user(spine):
    spine.audit("ana", "login", "", "")
    spine.audit("iva", "login", "", "")

    rows = auditlog.search(spine, user="ana")
    assert len(rows) == 1
    assert rows[0]["user"] == "ana"


def test_search_filters_by_action_and_limit(spine):
    for i in range(3):
        spine.audit("ana", "note_add", f"client:{i}", "")
    spine.audit("ana", "login", "", "")

    rows = auditlog.search(spine, action="note_add", limit=2)
    assert len(rows) == 2
    assert all(r["action"] == "note_add" for r in rows)


def test_search_filters_by_client_entity(spine):
    spine.audit("ana", "note_add", "client:42", "")
    spine.audit("ana", "note_add", "client:7", "")

    rows = auditlog.search(spine, client="42")
    assert len(rows) == 1
    assert rows[0]["entity"] == "client:42"
