"""VAULT reconciliation: recognize moved/renamed files by content hash instead
of re-ingesting duplicates or losing the doc<->chunks link on a NAS reorg."""
import os
from pathlib import Path

from atlas.core import security

from atlas.docs import ingest as ingest_mod
from atlas.docs.ingest import _file_sha  # re-exported: vault.py's identity-hash helper

INGESTABLE_EXTS = {".pdf", ".docx", ".xlsx", ".txt", ".md"}


def resolve_scope(cfg, root: str) -> str:
    """Scope a scan root under cfg.nas_root or cfg.data_dir (realpath+commonpath
    escape check — same pattern as docs.eracun._resolve_dest)."""
    target = os.path.realpath(root)
    for base in (getattr(cfg, "nas_root", ""), getattr(cfg, "data_dir", "")):
        if not base:
            continue
        b = os.path.realpath(base)
        if security.path_under(target, b):
            return target
    raise ValueError(f"path traversal blocked: {root!r} outside nas_root/data_dir")


def _walk_scan(root: str) -> dict[str, str]:
    """{abs_path: file_sha256} for ingestable files under root. Symlinked
    entries resolving outside root are skipped (escape guard)."""
    scanned: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            if Path(fname).suffix.lower() not in INGESTABLE_EXTS:
                continue
            fpath = os.path.join(dirpath, fname)
            rp = os.path.realpath(fpath)
            if not security.path_under(rp, root):
                continue
            try:
                scanned[fpath] = _file_sha(fpath)
            except OSError:
                continue
    return scanned


def scan_directory(spine, root: str, ingest_new: bool = True) -> dict:
    root = os.path.realpath(root)
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {root!r}")

    scanned = _walk_scan(root)
    scanned_paths = set(scanned)
    scanned_shas = set(scanned.values())

    rows = [dict(r) for r in spine.read().execute(
        "SELECT id, path, file_sha, stale FROM documents").fetchall()]

    # Backfill pass: legacy docs (ingested before file_sha existed, or never
    # touched by a vault scan) get their identity hash now, BEFORE the by_sha
    # map is built — otherwise a legacy doc that moves is misclassified as
    # delete+new (loses doc_id/chunks, exactly what this module prevents).
    for r in rows:
        if not r["file_sha"] and r["path"] and os.path.exists(r["path"]):
            try:
                fsha = _file_sha(r["path"])
            except OSError:
                continue
            with spine.write() as c:
                c.execute("UPDATE documents SET file_sha=? WHERE id=?", (fsha, r["id"]))
            r["file_sha"] = fsha

    by_path = {r["path"]: r for r in rows if r["path"]}
    by_sha: dict[str, list[dict]] = {}
    for r in rows:
        if r["file_sha"]:
            by_sha.setdefault(r["file_sha"], []).append(r)

    result = {"new": 0, "moved": 0, "renamed": 0, "changed": 0, "deleted": 0,
              "unchanged": 0, "details": []}
    matched_ids: set[int] = set()

    for fpath, fsha in scanned.items():
        db_row = by_path.get(fpath)

        if db_row is not None:
            matched_ids.add(db_row["id"])
            if not db_row["file_sha"]:
                # backfill: pre-existing doc ingested before file_sha existed
                with spine.write() as c:
                    c.execute("UPDATE documents SET file_sha=?, stale=0 WHERE id=?",
                              (fsha, db_row["id"]))
                result["unchanged"] += 1
            elif db_row["file_sha"] == fsha:
                if db_row["stale"]:
                    with spine.write() as c:
                        c.execute("UPDATE documents SET stale=0 WHERE id=?", (db_row["id"],))
                result["unchanged"] += 1
            else:
                result["changed"] += 1
                result["details"].append({"type": "changed", "doc_id": db_row["id"], "path": fpath})
                if ingest_new:
                    ingest_mod.ingest_file(spine, fpath)
                    # old row's content no longer matches what's on disk at this
                    # path — supersede it so retrieval only surfaces the new one.
                    with spine.write() as c:
                        c.execute("UPDATE documents SET stale=1 WHERE id=?", (db_row["id"],))
            continue

        candidates = [c for c in by_sha.get(fsha, [])
                      if c["id"] not in matched_ids and c["path"] not in scanned_paths]
        if candidates:
            moved = candidates[0]
            old_path = moved["path"]
            with spine.write() as c:
                c.execute("UPDATE documents SET path=?, stale=0 WHERE id=?", (fpath, moved["id"]))
            matched_ids.add(moved["id"])
            kind = "renamed" if os.path.dirname(old_path) == os.path.dirname(fpath) else "moved"
            result[kind] += 1
            result["details"].append({"type": kind, "doc_id": moved["id"],
                                       "old_path": old_path, "new_path": fpath})
            continue

        result["new"] += 1
        result["details"].append({"type": "new", "path": fpath})
        if ingest_new:
            ingest_mod.ingest_file(spine, fpath)

    for r in rows:
        if r["id"] in matched_ids or r["stale"]:
            continue
        path = r["path"]
        if not path or not security.path_under(os.path.realpath(path), root):
            continue  # outside this scan's scope — leave alone
        if os.path.exists(path) or r["file_sha"] in scanned_shas:
            continue
        with spine.write() as c:
            c.execute("UPDATE documents SET stale=1 WHERE id=?", (r["id"],))
        result["deleted"] += 1
        result["details"].append({"type": "deleted", "doc_id": r["id"], "path": path})

    return result


def vault_status(spine) -> dict:
    active = spine.read().execute("SELECT COUNT(*) c FROM documents WHERE stale=0").fetchone()["c"]
    stale = spine.read().execute("SELECT COUNT(*) c FROM documents WHERE stale=1").fetchone()["c"]
    recent = spine.read().execute(
        "SELECT id, title, path, stale, created_at FROM documents ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    return {"active": active, "stale": stale, "total": active + stale,
            "recent": [dict(r) for r in recent]}
