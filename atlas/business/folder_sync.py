# Phase 2: synchronization of registered network folders into the database.
#
# For a folder with role 'propisi': a file in a subfolder (Zakoni/Pravilnici/Uredbe/...) gets
# doc_type = tier from the subfolder name so the retrieval authority is correct. A changed
# file (new content) -> new active version, the old one -> superseded (retrieval already
# filters status='active', so the old regulation drops out of the answers).

import os

from atlas.business import folders as folders_mod
from atlas.docs import ingest as ingest_mod
from atlas.docs.ingest import UnsupportedFormat
from atlas.rag.authority import _fold

# subfolder name (folded) -> authority tier (key from authority.AUTHORITY)
_TIER_BY_DIR = {
    "zakoni": "zakon", "zakon": "zakon",
    "pravilnici": "pravilnik", "pravilnik": "pravilnik",
    "uredbe": "uredba", "uredba": "uredba",
    "misljenja": "misljenje_porezna", "misljenje": "misljenje_porezna",
    "misljenja porezne": "misljenje_porezna",
    "nn": "nn_objava", "narodne novine": "nn_objava",
    "kolektivni ugovori": "kolektivni_ugovor", "kolektivni ugovor": "kolektivni_ugovor",
}


def _subfolder_tier(folder_path: str, file_path: str) -> str | None:
    """Tier from the first subfolder under the registered folder (e.g. .../Propisi/Pravilnici/x.pdf
    -> 'pravilnik'). A file directly in the root (no subfolder) -> None (auto-detection)."""
    rel = os.path.relpath(file_path, folder_path)
    parts = rel.split(os.sep)
    if len(parts) < 2:
        return None
    return _TIER_BY_DIR.get(_fold(parts[0]))


def _reconcile_path(spine, path: str) -> int:
    """Idempotent: for a single path keep ONLY the newest active document
    (highest id), older active ones -> superseded, set version/pred on the new one, with
    predecessor = the highest of the older ones. Works even when a previous sync crashed between
    the insert and the supersede (crash-safe) - the next call cleans up."""
    with spine.write() as c:
        rows = c.execute(
            "SELECT id, version FROM documents WHERE path=? AND status='active' ORDER BY id",
            (path,),
        ).fetchall()
        if len(rows) <= 1:
            return 0
        keep, older = rows[-1], rows[:-1]  # highest id = newest
        maxv = max((r["version"] or 1) for r in rows)
        for r in older:
            c.execute("UPDATE documents SET status='superseded' WHERE id=?", (r["id"],))
        c.execute("UPDATE documents SET supersedes=?, version=? WHERE id=?",
                  (older[-1]["id"], maxv + 1, keep["id"]))
    return len(older)


def _active_file_sha(spine, path: str) -> str | None:
    r = spine.read().execute(
        "SELECT file_sha FROM documents WHERE path=? AND status='active' ORDER BY id DESC LIMIT 1",
        (path,),
    ).fetchone()
    return r["file_sha"] if r else None


def sync_folder(spine, cfg, folder: dict) -> dict:
    counts = {"ingested": 0, "skipped": 0, "superseded": 0, "errors": []}
    role = folder.get("role") or "ostalo"
    # Revalidate the root EVERY time (the mount may have dropped, the root removed from mount_roots,
    # or the folder replaced with a symlink after registration).
    try:
        path = folders_mod._scoped(cfg, folder["path"])
    except ValueError as e:
        counts["errors"].append(f"mapa izvan dozvoljenih korijena: {e}")
        return counts
    if not os.path.isdir(path) or os.path.islink(folder["path"]):
        counts["errors"].append(f"nedostupna mapa: {folder['path']}")
        return counts
    if role == "klijenti":
        return counts  # client folders are handled by onboarding
    roots = cfg.mount_roots or []
    for root, dirs, files in os.walk(path, followlinks=False):
        # do not descend into symlinked subfolders (they can lead outside the root)
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for fname in files:
            fp = os.path.join(root, fname)
            # skip symlink files and anything whose realpath leads outside the root (TOCTOU/escape)
            if os.path.islink(fp) or not folders_mod._under_a_root(os.path.realpath(fp), roots):
                counts["skipped"] += 1
                continue
            dtype = _subfolder_tier(path, fp) if role == "propisi" else None
            try:
                fsha = ingest_mod._file_sha(fp)
                if _active_file_sha(spine, fp) == fsha:
                    counts["skipped"] += 1  # unchanged (by FILE CONTENT)
                else:
                    doc_id = ingest_mod.ingest_file(spine, fp, doc_type=dtype)
                    counts["skipped" if doc_id is None else "ingested"] += 1
            except UnsupportedFormat:
                counts["skipped"] += 1
                continue
            except Exception as e:  # one bad file does not break the synchronization
                counts["errors"].append(f"{fp}: {e}")
                continue
            counts["superseded"] += _reconcile_path(spine, fp)  # always, idempotent
    return counts


def sync_all(spine, cfg) -> dict:
    total = {"folders": 0, "ingested": 0, "skipped": 0, "superseded": 0, "errors": []}
    for folder in folders_mod.list_folders(spine):
        if not folder.get("enabled"):
            continue
        total["folders"] += 1
        r = sync_folder(spine, cfg, folder)
        for k in ("ingested", "skipped", "superseded"):
            total[k] += r[k]
        total["errors"].extend(r["errors"])
        with spine.write() as c:
            c.execute("UPDATE folders SET last_synced=datetime('now') WHERE id=?", (folder["id"],))
    return total
