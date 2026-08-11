"""Vector sidecar — brute-force cosine search over SQLite-stored numpy arrays.

Keyed by (scope, hash). L2-normalized upstream → dot product = cosine similarity.
ponytail: brute-force is fine for thousands of rows. sqlite-vec only if measurably slow.
"""

import hashlib
import numpy as np

from .store import store
from .fts import fold, key


def upsert(content: str, scope: str, vec):
    v = np.asarray(vec, dtype=np.float32)
    store().execute(
        "INSERT OR REPLACE INTO mem_vectors(scope, hash, dim, vec) VALUES(?, ?, ?, ?)",
        (scope, key(content), int(v.shape[0]), v.tobytes()))
    store().commit()


def delete(scope: str, content: str):
    from .fts import delete as fts_delete
    fts_delete(scope, content)
    store().execute("DELETE FROM mem_vectors WHERE scope=? AND hash=?",
                    (scope, key(content)))
    store().commit()


def search(scope: str, qvec, k: int = 50) -> list[tuple[str, float]]:
    """→ [(hash, cosine_similarity)] for this scope, best first."""
    q = np.asarray(qvec, dtype=np.float32)
    qd = q.shape[0]
    rows = store().execute(
        "SELECT hash, dim, vec FROM mem_vectors WHERE scope=?", (scope,)).fetchall()
    scored = [(h, float(q @ np.frombuffer(b, dtype=np.float32)))
              for h, d, b in rows if d == qd]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def index(content: str, scope: str):
    """Sidecar lanes for one memory: keyword always, vector only if embedder is on."""
    from .fts import upsert as fts_upsert
    fts_upsert(scope, content)
    from .embed import available, embed_passages
    if not available():
        return
    try:
        v = embed_passages([content])
        if v is not None:
            upsert(content, scope, v[0])
    except Exception:
        pass


def ranks(scope: str, query: str, contents: list) -> dict[int, int]:
    """Map each content's list-index to its vector rank (0=best) for `query`."""
    if not query:
        return {}
    from .embed import available, embed_query
    if not available():
        return {}
    try:
        qv = embed_query(query)
        if qv is None:
            return {}
        pos = {h: r for r, (h, _) in enumerate(search(scope, qv, k=50))}
        return {i: pos[key(c)] for i, c in enumerate(contents) if key(c) in pos}
    except Exception:
        return {}
