"""Database backups. VACUUM INTO produces a consistent, compact snapshot even
against a live WAL database (without stopping the server). Restore runs via the
CLI while the server is stopped — a live restore with open connections is not safe."""
import glob
import os
import shutil
import sqlite3
from datetime import datetime

_PREFIX = "atlas-"
_SUFFIX = ".db"


def _backup_dir(cfg) -> str:
    d = os.path.join(cfg.data_dir, "backups")
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # contains a full PII snapshot
    except OSError:
        pass
    return d


def create_backup(cfg, stamp: str | None = None) -> dict:
    """Consistent snapshot via VACUUM INTO. Writes to .tmp then atomic os.replace
    (Codex: writing directly to the final name can leave an incomplete file on interruption)."""
    dest_dir = _backup_dir(cfg)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{_PREFIX}{stamp}{_SUFFIX}"
    dest = os.path.join(dest_dir, name)
    tmp = os.path.join(dest_dir, f".{_PREFIX}{stamp}.{os.getpid()}.tmp")
    if os.path.exists(tmp):
        os.unlink(tmp)  # VACUUM INTO requires a non-existent target
    con = sqlite3.connect(cfg.db_path, timeout=60)
    try:
        con.execute("VACUUM INTO ?", (tmp,))  # SQLite >=3.27; Python 3.11 ships a newer one
    finally:
        con.close()
    if not verify_backup(tmp):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError("backup nije prošao verifikaciju (quick_check)")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, dest)  # atomic within the same directory
    return {"name": name, "path": dest, "size": os.path.getsize(dest)}


def list_backups(cfg) -> list[dict]:
    d = _backup_dir(cfg)
    out = []
    for p in sorted(glob.glob(os.path.join(d, f"{_PREFIX}*{_SUFFIX}")), reverse=True):
        out.append({"name": os.path.basename(p), "size": os.path.getsize(p),
                    "mtime": int(os.path.getmtime(p))})
    return out


def prune(cfg, keep: int = 14) -> int:
    """Keep the last `keep` backups, delete the rest. Returns the number deleted."""
    keep = max(1, keep)  # never delete ALL backups (Codex: keep=0 edge case)
    d = _backup_dir(cfg)
    files = sorted(glob.glob(os.path.join(d, f"{_PREFIX}*{_SUFFIX}")))  # oldest first
    removed = 0
    for p in files[:-keep] if len(files) > keep else []:
        try:
            os.unlink(p)
            removed += 1
        except OSError:
            pass
    return removed


_REQUIRED_TABLES = {"users", "clients", "documents"}


def verify_backup(path: str) -> bool:
    """A valid ATLAS snapshot: existing file, quick_check ok, and contains the
    ATLAS schema. Opens read-only so sqlite3.connect does NOT create an empty
    database on a typo (Codex: an empty database would otherwise pass quick_check)."""
    if not os.path.isfile(path):
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            r = con.execute("PRAGMA quick_check").fetchone()
            if not (bool(r) and r[0] == "ok"):
                return False
            tabs = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            return _REQUIRED_TABLES <= tabs
        finally:
            con.close()
    except Exception:
        return False


def resolve_backup(cfg, name: str) -> str:
    """Safely resolve a backup name to a path INSIDE the backup directory (no traversal)."""
    d = os.path.realpath(_backup_dir(cfg))
    safe = os.path.basename(name)  # drop all path components
    path = os.path.realpath(os.path.join(d, safe))
    if os.path.dirname(path) != d or not os.path.isfile(path):
        raise ValueError(f"nepoznata sigurnosna kopija: {name!r}")
    return path


def restore_backup(cfg, path: str) -> dict:
    """Replace the live database with a backup. ONLY while the server is stopped
    (CLI). Codex hardening: verifies the ATLAS schema (not just quick_check),
    a consistent prerestore snapshot (VACUUM INTO — includes WAL), copies to a
    temp file then ATOMIC os.replace (an interruption leaves no half database)."""
    if not verify_backup(path):
        raise ValueError("nije valjana ATLAS baza (nepostojeća / prazna / kriva shema)")
    db = cfg.db_path
    if os.path.exists(db):
        pre = db + ".prerestore"
        if os.path.exists(pre):
            os.unlink(pre)
        con = sqlite3.connect(db, timeout=60)
        try:
            con.execute("VACUUM INTO ?", (pre,))  # consistent, incl. WAL
        except Exception:
            shutil.copy2(db, pre)  # fallback
        finally:
            con.close()
    tmp = db + f".restore.{os.getpid()}.tmp"
    shutil.copy2(path, tmp)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, db)  # atomic
    for ext in ("-wal", "-shm"):
        try:
            os.unlink(db + ext)
        except OSError:
            pass
    return {"restored_from": os.path.basename(path), "db": db}
