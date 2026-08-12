"""Excel export on demand. Supported exports are fixed (clients, obligations) — when
the request is incomplete, `clarify` returns questions (the AI asks the user). Client
visibility is respected. Anti formula-injection on every cell.
"""
import re
import secrets
from datetime import date
from pathlib import Path

from atlas.business import obveze
from atlas.core import optional, security

EXPORTS = ("klijenti", "obveze")
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n")
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def _cell(v):
    if isinstance(v, str) and v[:1] in _FORMULA_LEAD:
        return "'" + v
    return v


def clarify(sto: str | None, period: str | None) -> list[str]:
    """Return the questions still missing before the export can be made (empty = ready)."""
    if not sto or sto not in EXPORTS:
        return [f"Što izvesti? Mogu: {', '.join(EXPORTS)}."]
    if sto == "obveze" and (not period or not _PERIOD_RE.match(period)):
        return ["Za koji mjesec (format GGGG-MM, npr. 2026-08)?"]
    return []


def _exports_dir(cfg) -> Path:
    d = Path(cfg.data_dir) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build(spine, cfg, sto: str, period: str | None, visible) -> tuple[str, int]:
    """Build the xlsx for the requested export (visibility-scoped). Return (token, row_count).
    `visible` = set of visible client_id, or None (all)."""
    if clarify(sto, period):
        raise ValueError("izvoz nije potpuno određen")
    openpyxl = optional.need("openpyxl", "Excel izvoz")
    if openpyxl is None:
        raise ValueError("openpyxl nije instaliran (pip install atlas[full])")
    wb = openpyxl.Workbook()
    ws = wb.active
    rows = 0
    if sto == "klijenti":
        ws.title = "Klijenti"
        ws.append(["Naziv", "OIB", "PDV", "Sustav"])
        for r in spine.read().execute(
                "SELECT id, name, oib, pdv_status, regime FROM clients ORDER BY name").fetchall():
            if visible is not None and r["id"] not in visible:
                continue
            ws.append([_cell(r["name"]), _cell(r["oib"]), _cell(r["pdv_status"]), _cell(r["regime"])])
            rows += 1
    else:  # obligations for the period
        ws.title = f"Obveze {period}"
        ws.append(["Klijent", "Vrsta", "Poslano", "Tko", "Kad"])
        for k in obveze.active_kinds(spine):
            obveze.ensure_period(spine, k, period)
            for r in obveze.list_period(spine, k, period):
                if visible is not None and r["client_id"] is not None and r["client_id"] not in visible:
                    continue
                ws.append([_cell(r["client"]), _cell(k), "da" if r["sent"] else "ne",
                           _cell(r.get("sent_by") or ""), _cell(r.get("sent_at") or "")])
                rows += 1
    token = secrets.token_urlsafe(16)
    wb.save(str(_exports_dir(cfg) / f"{token}.xlsx"))
    return token, rows


def path_for(cfg, token: str) -> str | None:
    """Safe path to the exported file (token = safe filename, path-scoped)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token or ""):
        return None
    target = (_exports_dir(cfg) / f"{token}.xlsx").resolve()
    if not security.path_under(str(target), str(_exports_dir(cfg).resolve())):
        return None
    return str(target) if target.is_file() else None
