"""GDPR sweep: delete every row across user-data tables matching a search term."""
import hashlib

# ponytail: LIKE substring match is O(rows) full scan, no index — fine at this
# scale (single-tenant SQLite, sweep is rare/manual). Upgrade path: add
# indexes on hot columns if forget() ever runs on a hot path.
SIMPLE_TABLES = {
    "clients": ("name", "oib", "email", "phone", "owner"),
    "notes": ("body", "author"),
    "eracuni": ("supplier_oib", "customer_oib", "raw_path"),
    "interactions": ("query", "answer", "user"),
    "knowledge": ("question", "answer", "tags"),
    "memory": ("key", "value"),
    "expiry_items": ("label",),
    "audit_log": ("detail", "entity", "user"),
    "notifications": ("body",),
    "reminders": ("body", "user"),
    "feedback": ("query", "comment"),
}

# subquery predicates (no Python-side id lists — sidesteps SQLite's ~999 bound
# param ceiling entirely instead of batching IN(...) lists).
_DOC_WHERE = "title LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' OR source_url LIKE ? ESCAPE '\\'"
_NODE_WHERE = "value LIKE ? ESCAPE '\\'"


def _escape_pattern(term: str) -> str:
    """Build a LIKE pattern with %, _ and the escape char itself escaped, so a
    term containing literal % or _ is matched verbatim instead of acting as a
    wildcard (which would over-match and DELETE unrelated rows)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _count(c, table, cols, pattern):
    where = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in cols)
    return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", [pattern] * len(cols)).fetchone()[0]


def _delete(c, table, cols, pattern):
    where = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in cols)
    c.execute(f"DELETE FROM {table} WHERE {where}", [pattern] * len(cols))


def forget(spine, term: str, dry: bool = False) -> dict:
    pattern = _escape_pattern(term)
    result: dict[str, int] = {}

    with spine.write() as c:
        result["documents"] = c.execute(
            f"SELECT COUNT(*) FROM documents WHERE {_DOC_WHERE}", (pattern,) * 3).fetchone()[0]
        result["chunks"] = c.execute(
            f"SELECT COUNT(*) FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE {_DOC_WHERE})",
            (pattern,) * 3).fetchone()[0]
        if not dry:
            # chunks before documents: keeps chunks_fts trigger-consistent and
            # avoids orphaning chunks under a still-matching-but-not-yet-
            # deleted parent mid-sweep.
            c.execute(
                f"DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE {_DOC_WHERE})",
                (pattern,) * 3)
            c.execute(f"DELETE FROM documents WHERE {_DOC_WHERE}", (pattern,) * 3)

        result["kg_nodes"] = c.execute(
            f"SELECT COUNT(*) FROM kg_nodes WHERE {_NODE_WHERE}", (pattern,)).fetchone()[0]
        result["kg_edges"] = c.execute(
            f"SELECT COUNT(*) FROM kg_edges WHERE src IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE}) "
            f"OR dst IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE})",
            (pattern, pattern)).fetchone()[0]
        if not dry:
            c.execute(
                f"DELETE FROM kg_edges WHERE src IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE}) "
                f"OR dst IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE})",
                (pattern, pattern))
            c.execute(f"DELETE FROM kg_nodes WHERE {_NODE_WHERE}", (pattern,))

        for table, cols in SIMPLE_TABLES.items():
            n = _count(c, table, cols, pattern)
            result[table] = n
            if not dry and n:
                _delete(c, table, cols, pattern)

        if not dry:
            # Proof-of-erasure row, written AFTER the sweep so it can't be
            # swept by its own audit_log match, and redacted (hash, not the
            # raw term) so the erased PII isn't reintroduced into the DB by
            # the very row that proves it was erased.
            digest = hashlib.sha256(term.encode()).hexdigest()[:16]
            total = sum(result.values())
            c.execute(
                "INSERT INTO audit_log(user,action,entity,detail) VALUES(?,?,?,?)",
                ("system", "gdpr_forget", "gdpr_sweep", f"gdpr_forget hash={digest} rows={total}"))

    if not dry:
        with spine.write() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    return result
