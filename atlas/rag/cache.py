"""Query cache — 24h TTL, keyed by sha256 of normalized query."""
import hashlib
import re


def _norm(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def _hash(query: str, org_id=None) -> str:
    # org je dio ključa — isti upit u dvije organizacije NIKAD ne dijeli odgovor
    key = f"{'' if org_id is None else org_id}|{_norm(query)}"
    return hashlib.sha256(key.encode()).hexdigest()


def get(spine, query: str, org_id=None) -> str | None:
    row = spine.read().execute(
        "SELECT answer FROM query_cache WHERE qhash=? AND at > datetime('now','-24 hours')",
        (_hash(query, org_id),),
    ).fetchone()
    return row["answer"] if row else None


def put(spine, query: str, answer: str, meta: str = "", org_id=None) -> None:
    with spine.write() as c:
        c.execute(
            "INSERT OR REPLACE INTO query_cache(qhash,query,answer,meta,at) VALUES(?,?,?,?,datetime('now'))",
            (_hash(query, org_id), query, answer, meta),
        )
