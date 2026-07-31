"""Hybrid retrieval: FTS5 (always) + dense vector (if available), fused with RRF."""
import re
from dataclasses import dataclass

from ragspine.rag import embed


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    title: str
    text: str
    score: float
    doc_type: str


def rrf(rank_lists: list[list[int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for lst in rank_lists:
        for rank, doc_id in enumerate(lst):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def _fts_query(q: str) -> str:
    tokens = re.findall(r"\w+", q)
    return " OR ".join(f'"{t}"' for t in tokens)


def search(spine, query: str, k: int = 8, freshness: bool = True) -> list[Hit]:
    fts_q = _fts_query(query)
    if not fts_q:
        return []

    conn = spine.read()
    fts_rows = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 30",
        (fts_q,),
    ).fetchall()
    rank_lists = [[r["rowid"] for r in fts_rows]]

    if embed.available():
        vec_ids = [cid for cid, _ in embed.query_vec(spine, query, 30)]
        if vec_ids:
            rank_lists.append(vec_ids)

    fused = rrf(rank_lists)
    if not fused:
        return []

    ids = list(fused.keys())
    placeholders = ",".join("?" * len(ids))
    freshness_sql = ""
    if freshness:
        freshness_sql = (
            " AND (d.stale IS NULL OR d.stale=0)"
            " AND (d.valid_until IS NULL OR d.valid_until='' OR d.valid_until >= date('now'))"
        )
    rows = conn.execute(
        f"""SELECT c.id AS chunk_id, c.doc_id, c.title, c.text, d.doc_type
            FROM chunks c JOIN documents d ON d.id = c.doc_id
            WHERE c.id IN ({placeholders}){freshness_sql}""",
        ids,
    ).fetchall()

    hits = [
        Hit(r["chunk_id"], r["doc_id"], r["title"], r["text"], fused[r["chunk_id"]], r["doc_type"])
        for r in rows
    ]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]
