"""Sigurnosne kopije baze. VACUUM INTO daje konzistentan, kompaktan snapshot i
uz živu WAL bazu (bez zaustavljanja servera). Restore ide preko CLI-ja dok je
server zaustavljen — živi restore uz otvorene konekcije nije siguran."""
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
        os.chmod(d, 0o700)  # sadrži pun PII snapshot
    except OSError:
        pass
    return d


def create_backup(cfg, stamp: str | None = None) -> dict:
    """Konzistentan snapshot preko VACUUM INTO. Piše u .tmp pa atomski os.replace
    (Codex: izravan upis u konačno ime može ostaviti nepotpun fajl na prekidu)."""
    dest_dir = _backup_dir(cfg)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{_PREFIX}{stamp}{_SUFFIX}"
    dest = os.path.join(dest_dir, name)
    tmp = os.path.join(dest_dir, f".{_PREFIX}{stamp}.{os.getpid()}.tmp")
    if os.path.exists(tmp):
        os.unlink(tmp)  # VACUUM INTO traži nepostojeći cilj
    con = sqlite3.connect(cfg.db_path, timeout=60)
    try:
        con.execute("VACUUM INTO ?", (tmp,))  # SQLite ≥3.27; Python 3.11 ima noviji
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
    os.replace(tmp, dest)  # atomski unutar iste mape
    return {"name": name, "path": dest, "size": os.path.getsize(dest)}


def list_backups(cfg) -> list[dict]:
    d = _backup_dir(cfg)
    out = []
    for p in sorted(glob.glob(os.path.join(d, f"{_PREFIX}*{_SUFFIX}")), reverse=True):
        out.append({"name": os.path.basename(p), "size": os.path.getsize(p),
                    "mtime": int(os.path.getmtime(p))})
    return out


def prune(cfg, keep: int = 14) -> int:
    """Zadrži zadnjih `keep` kopija, ostale obriši. Vraća broj obrisanih."""
    keep = max(1, keep)  # nikad ne obriši SVE kopije (Codex: keep=0 rubni slučaj)
    d = _backup_dir(cfg)
    files = sorted(glob.glob(os.path.join(d, f"{_PREFIX}*{_SUFFIX}")))  # stariji prvi
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
    """Valjan ATLAS snapshot: postojeća datoteka, quick_check ok, i sadrži
    ATLAS shemu. Otvara read-only da sqlite3.connect NE stvori praznu bazu na
    tipfeleru (Codex: prazna baza bi inače prošla quick_check)."""
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
    """Sigurna razrješba imena kopije na putanju UNUTAR backup mape (bez traversala)."""
    d = os.path.realpath(_backup_dir(cfg))
    safe = os.path.basename(name)  # odbaci sve komponente putanje
    path = os.path.realpath(os.path.join(d, safe))
    if os.path.dirname(path) != d or not os.path.isfile(path):
        raise ValueError(f"nepoznata sigurnosna kopija: {name!r}")
    return path


def restore_backup(cfg, path: str) -> dict:
    """Zamijeni živu bazu kopijom. SAMO dok je server zaustavljen (CLI). Codex
    hardening: verificira ATLAS shemu (ne samo quick_check), konzistentan
    prerestore snapshot (VACUUM INTO — uključuje WAL), kopira u temp pa ATOMSKI
    os.replace (prekid ne ostavlja polovičnu bazu)."""
    if not verify_backup(path):
        raise ValueError("nije valjana ATLAS baza (nepostojeća / prazna / kriva shema)")
    db = cfg.db_path
    if os.path.exists(db):
        pre = db + ".prerestore"
        if os.path.exists(pre):
            os.unlink(pre)
        con = sqlite3.connect(db, timeout=60)
        try:
            con.execute("VACUUM INTO ?", (pre,))  # konzistentno, uklj. WAL
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
    os.replace(tmp, db)  # atomski
    for ext in ("-wal", "-shm"):
        try:
            os.unlink(db + ext)
        except OSError:
            pass
    return {"restored_from": os.path.basename(path), "db": db}
