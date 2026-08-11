"""Embedding wrapper — local-only, zero-API-key, CPU-friendly BGE-small (33 MB).

L2-normalized vectors so cosine similarity = dot product.
ponytail: fastembed is already installed in this environment. No new deps.
"""

import numpy as np

_model = None


def _load():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding("BAAI/bge-small-en-v1.5")


def available() -> bool:
    try:
        _load()
        return True
    except Exception:
        return False


def embed_passages(texts: list[str]):
    """→ list of numpy float32 arrays, L2-normalized, or None on failure."""
    if not texts:
        return None
    try:
        _load()
        vecs = list(_model.embed(texts))
        result = []
        for v in vecs:
            arr = np.asarray(v, dtype=np.float32)
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm
            result.append(arr)
        return result
    except Exception:
        return None


def embed_query(text: str):
    """Single query vector, L2-normalized, or None."""
    if not text:
        return None
    r = embed_passages([text])
    return r[0] if r else None
