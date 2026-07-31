"""Query cache — 24h TTL, keyed by sha256 of normalized query."""
import hashlib
import re


def _norm(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def _hash(query: str) -> str:
    return hashlib.sha256(_norm(query).encode()).hexdigest()


def get(spine, query: str) -> str | None:
    row = spine.read().execute(
        "SELECT answer FROM query_cache WHERE qhash=? AND at > datetime('now','-24 hours')",
        (_hash(query),),
    ).fetchone()
    return row["answer"] if row else None


def put(spine, query: str, answer: str, meta: str = "") -> None:
    with spine.write() as c:
        c.execute(
            "INSERT OR REPLACE INTO query_cache(qhash,query,answer,meta,at) VALUES(?,?,?,?,datetime('now'))",
            (_hash(query), query, answer, meta),
        )
