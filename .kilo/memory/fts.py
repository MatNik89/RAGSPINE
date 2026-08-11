"""FTS5 keyword lane — diacritic-folded, BM25-ranked.

Every save() indexes into FTS. Every recall() fuses FTS rank with vector rank via RRF.
ponytail: FTS5 ships with SQLite. Zero deps.
"""

import hashlib
import re

_FOLD = str.maketrans("čćžšđČĆŽŠĐ", "cczsdCCZSD")


def fold(text: str) -> str:
    return text.translate(_FOLD).lower()


def key(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def upsert(scope: str, content: str):
    from .store import store
    folded = fold(content)
    store().execute(
        "INSERT INTO mem_fts(content, scope) VALUES(?, ?)",
        (folded, scope))


def delete(scope: str, content: str):
    from .store import store
    store().execute(
        "INSERT INTO mem_fts(mem_fts, content, scope) VALUES('delete', ?, ?)",
        (fold(content), scope))


def search(scope: str, query: str, limit: int = 50) -> list[tuple[str, float]]:
    """→ [(content_hash, bm25_score)] for this scope, best first."""
    from .store import store
    folded = fold(query)
    # sanitize FTS5 query — keep alphanumeric tokens only
    tokens = [t for t in re.findall(r"[a-z0-9]+", folded) if len(t) > 1]
    if not tokens:
        return []
    fts_query = " OR ".join(f'"{t}"*' for t in tokens)
    try:
        rows = store().execute(
            "SELECT content, scope, rank FROM mem_fts WHERE mem_fts MATCH ? AND scope = ? "
            "ORDER BY rank LIMIT ?",
            (fts_query, scope, limit)).fetchall()
    except Exception:
        return []
    return [(key(c), -sco) for c, _s, sco in rows]


def ranks(scope: str, query: str, contents: list) -> dict[int, int]:
    """Map each content's list-index to its FTS rank (0=best). Empty if no keyword matches."""
    if not query:
        return {}
    hits = search(scope, query, limit=len(contents) * 2)
    pos = {h: r for r, (h, _) in enumerate(hits)}
    need = {key(c): i for i, c in enumerate(contents)}
    return {i: pos[h] for h, i in need.items() if h in pos}
