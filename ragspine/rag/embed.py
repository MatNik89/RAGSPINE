"""Optional dense-vector index (fastembed + sqlite-vec). No-op wherever either
package or the model itself is unavailable — callers never need to branch."""
import logging
import os
from pathlib import Path

from ragspine.config import get_config

log = logging.getLogger(__name__)


def _cache_dir(cfg) -> str:
    """RAGSPINE-vlastiti TRAJNI cache modela pod data_dir. Bez ovoga fastembed
    piše u /tmp/fastembed_cache koji reboot/tmpreaper čisti — pa se skinuti
    model 'izgubi' i vektorska tiho padne na FTS. Izolirano od drugih alata."""
    d = Path(cfg.data_dir) / "fastembed"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)

_model = None
_model_failed = False


def available() -> bool:
    """True only if both libs import. Never loads the model (no network, no cost)."""
    try:
        import fastembed  # noqa: F401
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model():
    """Lazy singleton. ponytail: local_files_only unless RAGSPINE_TEST_EMBED=1, so a
    missing/uncached model degrades to a silent no-op instead of a surprise
    multi-hundred-MB download mid-request. Upgrade path: explicit
    `ragspine setup --download-models` warmup command (Task 36)."""
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    cfg = get_config()
    try:
        from fastembed import TextEmbedding
        local_only = os.environ.get("RAGSPINE_TEST_EMBED") != "1"
        _model = TextEmbedding(cfg.embed_model, cache_dir=_cache_dir(cfg),
                               local_files_only=local_only)
    except Exception:
        log.warning("embed model load failed (%s) — vector search disabled", cfg.embed_model, exc_info=True)
        _model_failed = True
        _model = None
    return _model


def download_model(cfg=None) -> dict:
    """Eksplicitni warmup: povuci embedding model (jednom, svjesno) da vektorska
    pretraga proradi. Bez ovoga _get_model radi local_files_only i tiho no-op-a.
    Vraća {ok, model, dim} ili {ok:False, error}."""
    if not available():
        return {"ok": False, "error": "fastembed/sqlite-vec nisu instalirani (pip install .[full])"}
    if cfg is None:
        cfg = get_config()
    global _model, _model_failed
    try:
        from fastembed import TextEmbedding
        _model = TextEmbedding(cfg.embed_model, cache_dir=_cache_dir(cfg),
                               local_files_only=False)  # dozvoli download
        _model_failed = False
        dim = len(next(iter(_model.embed(["query: test"]))))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "model": cfg.embed_model}
    return {"ok": True, "model": cfg.embed_model, "dim": dim}


def _load_vec(conn):
    import sqlite_vec
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _is_e5(model_name: str) -> bool:
    """e5 obitelj traži 'query: '/'passage: ' prefikse; drugi modeli (MiniLM,
    mpnet…) ih tretiraju kao doslovni tekst i to im kvari kvalitetu."""
    return "e5" in (model_name or "").lower()


def _passage(text: str, cfg) -> str:
    return f"passage: {text}" if _is_e5(cfg.embed_model) else text


def _query_text(text: str, cfg) -> str:
    return f"query: {text}" if _is_e5(cfg.embed_model) else text


def model_dim(cfg) -> int | None:
    """Stvarna dimenzija aktivnog modela (jedan probe-embedding). None ako model
    nije dostupan. Bez ovoga vec tablica bi bila fiksna na 1024 i pucala čim se
    promijeni embed model."""
    m = _get_model()
    if m is None:
        return None
    return len(next(iter(m.embed([_query_text("x", cfg)]))))


def ensure_vec_table(spine):
    if not available():
        return
    cfg = get_config()
    dim = model_dim(cfg)
    if dim is None:
        return
    # Ako je model (i time dimenzija) promijenjen, stara vec_chunks tablica ne
    # odgovara — presloži je (vektori su re-indeksabilni iz chunks).
    stored = spine.get_override("embed", "vec_dim")
    with spine.write() as c:
        _load_vec(c)
        if stored is not None and int(stored) != dim:
            c.execute("DROP TABLE IF EXISTS vec_chunks")
        c.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING "
            f"vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{dim}])"
        )
    if stored is None or int(stored) != dim:
        spine.set_override("embed", "vec_dim", str(dim))


def index_chunks(spine, chunk_ids: list[int]):
    if not available() or not chunk_ids:
        return
    model = _get_model()
    if model is None:
        return
    placeholders = ",".join("?" * len(chunk_ids))
    rows = spine.read().execute(
        f"SELECT id, text FROM chunks WHERE id IN ({placeholders})", chunk_ids
    ).fetchall()
    if not rows:
        return
    import sqlite_vec
    cfg = get_config()
    vecs = list(model.embed([_passage(r["text"], cfg) for r in rows]))
    ensure_vec_table(spine)
    with spine.write() as c:
        _load_vec(c)
        for row, vec in zip(rows, vecs):
            c.execute("DELETE FROM vec_chunks WHERE chunk_id=?", (row["id"],))
            c.execute(
                "INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?, ?)",
                (row["id"], sqlite_vec.serialize_float32(list(vec))),
            )


def query_vec(spine, query: str, k: int) -> list[tuple[int, float]]:
    if not available():
        return []
    model = _get_model()
    if model is None:
        return []
    ensure_vec_table(spine)
    import sqlite_vec
    conn = spine.read()
    _load_vec(conn)
    qvec = next(iter(model.embed([_query_text(query, get_config())])))
    rows = conn.execute(
        "SELECT chunk_id, distance FROM vec_chunks WHERE embedding MATCH ? "
        "ORDER BY distance LIMIT ?",
        (sqlite_vec.serialize_float32(list(qvec)), k),
    ).fetchall()
    return [(r["chunk_id"], r["distance"]) for r in rows]
