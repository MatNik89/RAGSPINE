"""Knowledge lifecycle: draft -> active -> superseded (audit trail kept, never
deleted rows). Retrieval excludes anything not 'active' (see rag/retrieval.py).
"""
from ragspine.docs.ingest import ingest_text

STATUSES = ("draft", "active", "superseded", "deleted")


def _get_doc(spine, doc_id):
    row = spine.read().execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        raise ValueError(f"nepoznat dokument: {doc_id}")
    return row


def set_status(spine, doc_id, status: str, user: str = "system") -> None:
    if status not in STATUSES:
        raise ValueError(f"nepoznat status: {status!r} (dozvoljeno: {STATUSES})")
    _get_doc(spine, doc_id)
    with spine.write() as c:
        c.execute("UPDATE documents SET status=? WHERE id=?", (status, doc_id))
        c.execute("INSERT INTO audit_log(user,action,entity,detail) VALUES(?,?,?,?)",
                  (user, "status_change", f"document:{doc_id}", status))


def supersede(spine, old_doc_id: int, new_doc_id: int, user: str = "system") -> None:
    old = _get_doc(spine, old_doc_id)
    _get_doc(spine, new_doc_id)
    old_version = old["version"] or 1
    with spine.write() as c:
        c.execute("UPDATE documents SET status='superseded' WHERE id=?", (old_doc_id,))
        c.execute(
            "UPDATE documents SET status='active', supersedes=?, version=? WHERE id=?",
            (old_doc_id, old_version + 1, new_doc_id),
        )
        c.execute("INSERT INTO audit_log(user,action,entity,detail) VALUES(?,?,?,?)",
                  (user, "supersede", f"document:{new_doc_id}", f"supersedes:{old_doc_id}"))


def promote_draft(spine, doc_id: int, user: str = "system") -> None:
    doc = _get_doc(spine, doc_id)
    if doc["status"] != "draft":
        raise ValueError(f"dokument {doc_id} nije draft (status={doc['status']!r})")
    set_status(spine, doc_id, "active", user=user)


def stage_draft(spine, text: str, title: str, doc_type: str | None = None,
                 client_id=None, source_url: str = "", path: str = ""):
    doc_id = ingest_text(spine, text, title, doc_type=doc_type, client_id=client_id,
                          source_url=source_url, path=path)
    if doc_id is not None:
        with spine.write() as c:
            c.execute("UPDATE documents SET status='draft' WHERE id=?", (doc_id,))
    return doc_id


def version_history(spine, doc_id: int) -> list[dict]:
    """Chronological version list along the supersedes chain: walk back to the
    oldest ancestor, then forward through whoever superseded each doc."""
    conn = spine.read()
    doc = _get_doc(spine, doc_id)

    # walk back to the root (oldest ancestor)
    root = doc
    while root["supersedes"] is not None:
        parent = conn.execute("SELECT * FROM documents WHERE id=?", (root["supersedes"],)).fetchone()
        if parent is None:
            break
        root = parent

    # walk forward from root following whoever supersedes each doc
    chain = [root]
    current_id = root["id"]
    while True:
        nxt = conn.execute("SELECT * FROM documents WHERE supersedes=?", (current_id,)).fetchone()
        if nxt is None:
            break
        chain.append(nxt)
        current_id = nxt["id"]

    return [{"doc_id": r["id"], "version": r["version"] or 1, "status": r["status"],
             "title": r["title"]} for r in chain]


def active_version(spine, title_or_source: str) -> dict | None:
    row = spine.read().execute(
        "SELECT * FROM documents WHERE (title=? OR source_url=?) AND status='active'",
        (title_or_source, title_or_source),
    ).fetchone()
    if row is None:
        return None
    return {"doc_id": row["id"], "version": row["version"] or 1, "status": row["status"],
            "title": row["title"], "supersedes": row["supersedes"]}
