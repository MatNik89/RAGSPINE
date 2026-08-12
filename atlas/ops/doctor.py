"""Preflight checks: python/disk/ram/ntp/luks/ollama/ocr/optional-deps/db."""
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request

from atlas.core import optional
from atlas.core.llm import _ollama_alive
from atlas.core.subproc import run_isolated

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


def _check_admin_exists(cfg) -> dict:
    """Za produkciju mora postojati barem jedan korisnik — inače se nitko ne može
    prijaviti (svjež install)."""
    try:
        conn = sqlite3.connect(cfg.db_path, timeout=5)
        try:
            n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        return {"check": "korisnici", "ok": n > 0,
                "detail": f"{n} korisnika" if n else "nema korisnika — kreiraj (atlas auth add)"}
    except Exception as e:
        return {"check": "korisnici", "ok": True, "detail": f"nije provjereno ({e})"}


def _check_llm_configured(cfg) -> dict:
    """RAG bez LLM-a radi degradirano (samo regex/FTS) — javi ako provider fali."""
    try:
        from atlas.core.llm import load_oauth_token
        has = bool(cfg.llm_provider or cfg.llm_api_key or load_oauth_token())
    except Exception:
        has = bool(cfg.llm_provider or cfg.llm_api_key)
    return {"check": "llm_provider", "ok": has,
            "detail": cfg.llm_provider or ("konfiguriran" if has else "nije konfiguriran — RAG degradiran")}


def _check_nas_configured(cfg) -> dict:
    """Uredske funkcije (skeni, arhitektura, e-račun autosort) trebaju registriranu
    KLIJENTI mapu unutar dozvoljenih mount_roots."""
    if not cfg.mount_roots:
        return {"check": "nas", "ok": True, "detail": "mount_roots prazan (NAS funkcije isključene)"}
    try:
        conn = sqlite3.connect(cfg.db_path, timeout=5)
        try:
            r = conn.execute("SELECT COUNT(*) FROM folders WHERE role='klijenti'").fetchone()[0]
        finally:
            conn.close()
        return {"check": "nas", "ok": r > 0,
                "detail": "KLIJENTI mapa registrirana" if r else "nema role='klijenti' mape — registriraj"}
    except Exception as e:
        return {"check": "nas", "ok": True, "detail": f"nije provjereno ({e})"}


def _check_secret_perms(cfg) -> dict:
    """DB + secret smiju biti čitljivi samo vlasniku (0600) — inače drugi lokalni
    korisnici hosta vide PII/hasheve/JWT tajnu."""
    if os.name == "nt":
        return {"check": "perms", "ok": True, "detail": "n/a (Windows ACL)"}
    import stat
    bad = []
    for p in (cfg.db_path, os.path.join(cfg.data_dir, "secret")):
        try:
            mode = stat.S_IMODE(os.stat(p).st_mode)
            if mode & 0o077:
                bad.append(f"{os.path.basename(p)}={oct(mode)}")
        except OSError:
            pass
    from atlas.business import secretbox
    fp = secretbox.key_fingerprint(cfg)  # dijagnostika krivog ključa pri restoreu (Paperclip)
    base = "0600" if not bad else "preširoke dozvole: " + ", ".join(bad)
    return {"check": "perms", "ok": not bad,
            "detail": base + (f"; ključ={fp}" if fp else "")}


_CHECKS = [
    _check_python_version, _check_disk_space, _check_ram, _check_ntp, _check_luks,
    _check_ollama, _check_ocr_server, _check_optional_deps, _check_db_writable,
    _check_admin_exists, _check_llm_configured, _check_nas_configured, _check_secret_perms,
]

# ponytail: only these gate the CLI exit code — ollama/ocr_server/ntp/luks/
# optional_deps are informational (e.g. ollama down is normal on a
# cloud-LLM/OAuth host, not a real failure). Upgrade path: per-check
# severity field if more nuance is needed later.
REQUIRED_CHECKS = {"python_version", "disk_space", "db_writable"}


def run(cfg) -> list[dict]:
    return [fn(cfg) for fn in _CHECKS]


def required_ok(results: list[dict]) -> bool:
    return all(r["ok"] for r in results if r["check"] in REQUIRED_CHECKS)


def format_report(results: list[dict]) -> str:
    lines = [f"{'✓' if r['ok'] else '✗'} {r['check']}: {r['detail']}" for r in results]
    return "\n".join(lines)
