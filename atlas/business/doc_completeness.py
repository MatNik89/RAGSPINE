"""Completeness of mandatory documents: which MANDATORY types (client_doc_types)
a client does not yet have (documents.doc_type). Used by both the AI tool and the
dashboard panel.
"""


def missing_for_client(spine, client_id: int) -> list[str]:
    required = [r["doc_type_key"] for r in spine.read().execute(
        "SELECT doc_type_key FROM client_doc_types WHERE client_id=?", (client_id,)).fetchall()]
    present = {r["doc_type"] for r in spine.read().execute(
        "SELECT DISTINCT doc_type FROM documents WHERE client_id=?", (client_id,)).fetchall()}
    return [k for k in required if k not in present]


def clients_missing_docs(spine, visible=None) -> list[dict]:
    """All clients missing something (visibility-scoped). For the info panel."""
    out = []
    for r in spine.read().execute(
            "SELECT DISTINCT c.id, c.name FROM clients c "
            "JOIN client_doc_types t ON t.client_id = c.id "
            "WHERE c.active=1 ORDER BY c.name COLLATE NOCASE").fetchall():
        if visible is not None and r["id"] not in visible:
            continue
        missing = missing_for_client(spine, r["id"])
        if missing:
            out.append({"client_id": r["id"], "client": r["name"], "nedostaju": missing})
    return out
