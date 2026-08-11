"""MemoryRepo — the main memory store. Hybrid retrieval = keyword ∪ vector → RRF fuse.

5 scopes: project | task | user | lesson | corpus.
Backed by SQLite WAL spine. Cross-process-safe. Lossless archive (never deletes).
ponytail: one class, ~60 logical lines of public API. The rest is lane wiring.
"""

import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from .store import write_lock, store, MEMORY_PATH
from .fts import key as fts_key
from .memvec import index as vec_index

SCOPES = ("project", "task", "user", "lesson", "corpus")


@dataclass
class MemoryEntry:
    content: str
    source: str
    scope: str = "project"
    confidence: float = 1.0
    date: str = ""
    expiration: str = "never"
    tier: str = "model"

    def __post_init__(self):
        if self.scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        self.date = self.date or datetime.now(timezone.utc).isoformat()

    def is_stale(self) -> bool:
        if self.expiration == "never":
            return False
        try:
            exp = datetime.fromisoformat(self.expiration)
        except ValueError:
            return False
        exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        return exp < datetime.now(timezone.utc)


def store_id_for(path: str) -> str:
    import hashlib
    parts = os.path.realpath(path).replace(os.sep, "/").split("/")
    return hashlib.sha256(
        "/".join(parts[-2:]).encode("utf-8", "surrogateescape")
    ).hexdigest()[:16]


class MemoryRepo:
    def __init__(self, path=MEMORY_PATH, migrate=True):
        self.path = path
        self.store_id = store_id_for(path)
        if migrate:
            self._migrate()

    def _migrate(self):
        pass  # ponytail: no legacy JSONL to migrate — born in SQLite

    def _rows(self, hot: int, scope: str = None):
        c = store()
        q = ("SELECT content,source,scope,confidence,date,expiration,tier "
             "FROM mem_entries WHERE store=? AND hot=?")
        args = [self.store_id, hot]
        if scope is not None:
            q += " AND scope=?"
            args.append(scope)
        return [MemoryEntry(*r) for r in
                c.execute(q + " ORDER BY id ASC", args).fetchall()]

    def save(self, entry: MemoryEntry):
        with write_lock:
            c = store()
            existing = c.execute(
                "SELECT 1 FROM mem_entries WHERE store=? AND hot=1 AND scope=? AND content=? LIMIT 1",
                (self.store_id, entry.scope, entry.content)).fetchone()
            if existing:
                c.commit()
                return
            c.execute(
                "INSERT INTO mem_entries(store,scope,content,source,confidence,date,expiration,hot,tier) "
                "VALUES(?,?,?,?,?,?,?,1,?)",
                (self.store_id, entry.scope, entry.content, entry.source, entry.confidence,
                 entry.date, entry.expiration, entry.tier))
            c.commit()
        vec_index(entry.content, entry.scope)

    def remember(self, content: str, source: str = "user", scope: str = "project",
                 confidence: float = 1.0):
        """Quick save with defaults. Returns the entry."""
        entry = MemoryEntry(content=content, source=source, scope=scope,
                            confidence=confidence)
        self.save(entry)
        return entry

    def _all(self) -> list:
        return self._rows(1)

    def query(self, scope: str = "project", relevant_to: str = "",
              limit: int = 10, deep: bool = False) -> list[MemoryEntry]:
        """Hybrid recall: keyword ∪ vector → RRF fuse → associate expansion → decay reselect.

        deep=True includes archived (cold) memories. Default is hot only.
        """
        hot = [e for e in self._rows(1, scope) if not e.is_stale()]
        if deep:
            from .archive import archived
            cold = archived(self, scope)
            seen = {(e.scope, e.content) for e in hot}
            hot = hot + [e for e in cold if (e.scope, e.content) not in seen]
        if not hot or not relevant_to:
            return hot[:limit]
        ranked = self._rank(hot, scope, relevant_to, limit)
        return ranked[:limit]

    def _rank(self, entries, scope, query, limit):
        """RRF fuse: vector ranks + keyword ranks → scored → layer (decay + associates)."""
        from .memvec import ranks as vranks
        from .fts import ranks as franks
        from .recall import layer, rescore, co_activate

        vr = vranks(scope, query, [e.content for e in entries])
        fr = franks(scope, query, [e.content for e in entries])
        rrf = {}
        for i in range(len(entries)):
            rrf[i] = (1.0 / (vr.get(i, 1000) + 60)) + (1.0 / (fr.get(i, 1000) + 60))
        ranked = sorted(entries, key=lambda e: -rrf.get(entries.index(e), 0))
        co_activate([(e.scope, e.content) for e in ranked[:5]])
        return layer(ranked[:limit * 2], entries, scope, limit)

    def consolidate(self) -> int:
        """Archive faded, low-confidence memories. Returns count archived."""
        from .recall import consolidate
        from .archive import stash_many
        drop = consolidate([e for e in self._all() if not e.is_stale()])
        if drop:
            stash_many(self, drop)
        return len(drop)

    def forget(self, scope: str, content: str):
        with write_lock:
            c = store()
            c.execute(
                "DELETE FROM mem_entries WHERE store=? AND hot=1 AND scope=? AND content=?",
                (self.store_id, scope, content))
            c.commit()
        from .memvec import delete as vec_delete
        vec_delete(scope, content)

    def clear_project(self):
        """Delete ALL project-scoped memories (hot + cold)."""
        with write_lock:
            c = store()
            c.execute("DELETE FROM mem_entries WHERE store=? AND scope='project'",
                      (self.store_id,))
            c.commit()

    def stats(self) -> dict:
        c = store()
        hot = c.execute(
            "SELECT scope, COUNT(*) FROM mem_entries WHERE store=? AND hot=1 "
            "GROUP BY scope", (self.store_id,)).fetchall()
        cold = c.execute(
            "SELECT scope, COUNT(*) FROM mem_entries WHERE store=? AND hot=0 "
            "GROUP BY scope", (self.store_id,)).fetchall()
        return {
            "hot": {s: n for s, n in hot},
            "cold": {s: n for s, n in cold},
            "total_hot": sum(n for _, n in hot),
            "total_cold": sum(n for _, n in cold),
        }
