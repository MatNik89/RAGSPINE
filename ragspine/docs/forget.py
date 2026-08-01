"""GDPR sweep: delete every row across user-data tables matching a search term."""

# ponytail: LIKE substring match is O(rows) full scan, no index — fine at this
# scale (single-tenant SQLite, sweep is rare/manual). Upgrade path: add
# indexes on hot columns if forget() ever runs on a hot path.
SIMPLE_TABLES = {
    "clients": ("name", "oib", "email", "phone", "owner"),
    "notes": ("body", "author"),
    "eracuni": ("supplier_oib", "customer_oib", "raw_path"),
    "interactions": ("query", "answer", "user"),
    "knowledge": ("question", "answer"),
    "memory": ("key", "value"),
    "expiry_items": ("label",),
    "audit_log": ("detail", "entity", "user"),
    "notifications": ("body",),
}


def _count(c, table, cols, pattern):
    where = " OR ".join(f"{col} LIKE ?" for col in cols)
    return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", [pattern] * len(cols)).fetchone()[0]


def _delete(c, table, cols, pattern):
    where = " OR ".join(f"{col} LIKE ?" for col in cols)
    c.execute(f"DELETE FROM {table} WHERE {where}", [pattern] * len(cols))


def forget(spine, term: str, dry: bool = False) -> dict:
    pattern = f"%{term}%"
    result: dict[str, int] = {}

    with spine.write() as c:
        if not dry:
            # audit the request BEFORE sweeping, in the same transaction. The
            # row's own detail=term so it matches the audit_log sweep below
            # too — that's fine, the request is still atomic with its effect.
            c.execute("INSERT INTO audit_log(user,action,entity,detail) VALUES(?,?,?,?)",
                      ("system", "forget", "gdpr_sweep", term))

        doc_ids = [r["id"] for r in c.execute(
            "SELECT id FROM documents WHERE title LIKE ? OR path LIKE ? OR source_url LIKE ?",
            (pattern, pattern, pattern)).fetchall()]
        result["documents"] = len(doc_ids)
        if doc_ids:
            ph = ",".join("?" * len(doc_ids))
            result["chunks"] = c.execute(
                f"SELECT COUNT(*) FROM chunks WHERE doc_id IN ({ph})", doc_ids).fetchone()[0]
            if not dry:
                # chunks before documents: keeps chunks_fts trigger-consistent
                # and avoids orphaning chunks under a deleted doc mid-sweep.
                c.execute(f"DELETE FROM chunks WHERE doc_id IN ({ph})", doc_ids)
                c.execute(f"DELETE FROM documents WHERE id IN ({ph})", doc_ids)
        else:
            result["chunks"] = 0

        node_ids = [r["id"] for r in c.execute(
            "SELECT id FROM kg_nodes WHERE value LIKE ?", (pattern,)).fetchall()]
        result["kg_nodes"] = len(node_ids)
        if node_ids:
            ph = ",".join("?" * len(node_ids))
            result["kg_edges"] = c.execute(
                f"SELECT COUNT(*) FROM kg_edges WHERE src IN ({ph}) OR dst IN ({ph})",
                node_ids + node_ids).fetchone()[0]
            if not dry:
                c.execute(f"DELETE FROM kg_edges WHERE src IN ({ph}) OR dst IN ({ph})",
                          node_ids + node_ids)
                c.execute(f"DELETE FROM kg_nodes WHERE id IN ({ph})", node_ids)
        else:
            result["kg_edges"] = 0

        for table, cols in SIMPLE_TABLES.items():
            n = _count(c, table, cols, pattern)
            result[table] = n
            if not dry and n:
                _delete(c, table, cols, pattern)

    if not dry:
        with spine.write() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    return result
