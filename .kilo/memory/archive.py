"""Lossless archival — cold memory tier, hot=0 flag in mem_entries.

Consolidation ARCHIVES faded memories; they leave hot recall but stay searchable
via deep=True query. Ponytail: one boolean column, not a separate file.
"""

from .store import write_lock, store


def stash(repo, entry):
    """Move ONE hot entry to cold tier (hot=1 → hot=0). Idempotent."""
    stash_many(repo, [entry])


def stash_many(repo, entries):
    with write_lock:
        c = store()
        c.execute("BEGIN IMMEDIATE")
        try:
            for e in entries:
                row = c.execute(
                    "SELECT id FROM mem_entries WHERE store=? AND hot=1 AND scope=? AND content=? "
                    "AND source=? AND date=? AND expiration=? LIMIT 1",
                    (repo.store_id, e.scope, e.content, e.source, e.date, e.expiration)).fetchone()
                if row is None:
                    continue
                existing = c.execute(
                    "SELECT 1 FROM mem_entries WHERE store=? AND hot=0 AND scope=? AND content=? "
                    "LIMIT 1", (repo.store_id, e.scope, e.content)).fetchone()
                if existing:
                    c.execute("DELETE FROM mem_entries WHERE id=?", (row[0],))
                else:
                    c.execute("UPDATE mem_entries SET hot=0 WHERE id=?", (row[0],))
            c.commit()
        except Exception:
            c.rollback()
            raise


def archived(repo, scope=None):
    c = store()
    if scope is not None:
        rows = c.execute(
            "SELECT scope,content,source,confidence,date,expiration,tier "
            "FROM mem_entries WHERE store=? AND hot=0 AND scope=? ORDER BY id ASC",
            (repo.store_id, scope)).fetchall()
    else:
        rows = c.execute(
            "SELECT scope,content,source,confidence,date,expiration,tier "
            "FROM mem_entries WHERE store=? AND hot=0 ORDER BY id ASC",
            (repo.store_id,)).fetchall()
    return [_entry(r) for r in rows]


def _entry(row):
    from .memory import MemoryEntry
    return MemoryEntry(scope=row[0], content=row[1], source=row[2],
                       confidence=row[3], date=row[4], expiration=row[5], tier=row[6] or "model")
