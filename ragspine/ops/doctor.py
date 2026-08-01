"""Preflight checks: python/disk/ram/ntp/luks/ollama/ocr/optional-deps/db."""
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request

from ragspine.core import optional
from ragspine.core.llm import _ollama_alive
from ragspine.core.subproc import run_isolated

_MIN_DISK_BYTES = 1_000_000_000


def _check_python_version(cfg) -> dict:
    try:
        ok = sys.version_info >= (3, 11)
        v = sys.version_info
        return {"check": "python_version", "ok": ok, "detail": f"{v.major}.{v.minor}.{v.micro}"}
    except Exception as e:
        return {"check": "python_version", "ok": False, "detail": str(e)}


def _check_disk_space(cfg) -> dict:
    try:
        free = shutil.disk_usage(cfg.data_dir).free
        ok = free >= _MIN_DISK_BYTES
        return {"check": "disk_space", "ok": ok, "detail": f"{free / 1e9:.2f} GB slobodno"}
    except Exception as e:
        return {"check": "disk_space", "ok": False, "detail": str(e)}


def _check_ram(cfg) -> dict:
    try:
        path = "/proc/meminfo"
        if not os.path.exists(path):
            return {"check": "ram", "ok": True, "detail": "n/a"}
        with open(path) as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return {"check": "ram", "ok": True, "detail": f"{kb / 1024 / 1024:.2f} GB"}
        return {"check": "ram", "ok": True, "detail": "n/a"}
    except Exception as e:
        return {"check": "ram", "ok": False, "detail": str(e)}


def _check_ntp(cfg) -> dict:
    try:
        _rc, out, err = run_isolated(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5)
        val = out.strip()
        return {"check": "ntp", "ok": val == "yes", "detail": val or err.strip() or "nepoznato"}
    except FileNotFoundError:
        return {"check": "ntp", "ok": True, "detail": "nije provjereno (timedatectl nedostupan)"}
    except Exception as e:
        return {"check": "ntp", "ok": False, "detail": str(e)}


def _check_luks(cfg) -> dict:
    try:
        _rc, out, _err = run_isolated(["lsblk", "-o", "TYPE"], timeout=5)
        found = "crypt" in out
        detail = "LUKS enkripcija pronađena" if found else "LUKS enkripcija NIJE pronađena (upozorenje)"
        return {"check": "luks", "ok": True, "detail": detail}
    except FileNotFoundError:
        return {"check": "luks", "ok": True, "detail": "nije provjereno (lsblk nedostupan)"}
    except Exception as e:
        return {"check": "luks", "ok": False, "detail": str(e)}


def _check_ollama(cfg) -> dict:
    try:
        ok = _ollama_alive(cfg)
        return {"check": "ollama", "ok": ok, "detail": cfg.ollama_url if ok else "nedostupan"}
    except Exception as e:
        return {"check": "ollama", "ok": False, "detail": str(e)}


def _check_ocr_server(cfg) -> dict:
    try:
        if not cfg.ocr_url:
            return {"check": "ocr_server", "ok": True, "detail": "nije konfiguriran"}
        try:
            with urllib.request.urlopen(cfg.ocr_url, timeout=2):
                pass
            return {"check": "ocr_server", "ok": True, "detail": cfg.ocr_url}
        except (urllib.error.URLError, OSError):
            return {"check": "ocr_server", "ok": False, "detail": "nedostupan"}
    except Exception as e:
        return {"check": "ocr_server", "ok": False, "detail": str(e)}


def _check_optional_deps(cfg) -> dict:
    try:
        m = optional.missing()
        ok = len(m) == 0
        detail = "sve instalirano" if ok else ", ".join(f"{k} ({v})" for k, v in m.items())
        return {"check": "optional_deps", "ok": ok, "detail": detail}
    except Exception as e:
        return {"check": "optional_deps", "ok": False, "detail": str(e)}


def _check_db_writable(cfg) -> dict:
    try:
        conn = sqlite3.connect(cfg.db_path, timeout=5)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            status = row[0] if row else "?"
            return {"check": "db_writable", "ok": status == "ok", "detail": status}
        finally:
            conn.close()
    except Exception as e:
        return {"check": "db_writable", "ok": False, "detail": str(e)}


_CHECKS = [
    _check_python_version, _check_disk_space, _check_ram, _check_ntp, _check_luks,
    _check_ollama, _check_ocr_server, _check_optional_deps, _check_db_writable,
]


def run(cfg) -> list[dict]:
    return [fn(cfg) for fn in _CHECKS]


def format_report(results: list[dict]) -> str:
    lines = [f"{'✓' if r['ok'] else '✗'} {r['check']}: {r['detail']}" for r in results]
    return "\n".join(lines)
