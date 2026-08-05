"""SQL lane: template-matched NL -> parametrized SELECT for numeric queries.

Regex decides *which* fixed SQL template runs; any value pulled from the
query (period, N) is bound as a parameter, never string-interpolated into
SQL text.
"""
import re
from datetime import date

from ragspine.rag.router import _normalize

_MONTHS = {
    "sij": "01", "velj": "02", "ozuj": "03", "trav": "04", "svib": "05",
    "lip": "06", "srp": "07", "kolov": "08", "ruj": "09", "listop": "10",
    "stude": "11", "prosin": "12",
}


def _period(q: str) -> str | None:
    """Extract an optional 'YYYY-MM' period from the query, for a LIKE filter."""
    m = re.search(r"\b(20\d{2})-(\d{2})\b", q)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    year_m = re.search(r"\b(20\d{2})\b", q)
    for stem, num in _MONTHS.items():
        if stem in q:
            year = year_m.group(1) if year_m else date.today().strftime("%Y")
            return f"{year}-{num}"
    return None


def _ph(visible) -> str:
    return ",".join("?" * len(visible)) if visible else "NULL"


def _oib_cond(visible):
    """eracuni nemaju client_id — ograniči po OIB-u kupca na vidljive klijente."""
    if visible is None:
        return None, ()
    return f"customer_oib IN (SELECT oib FROM clients WHERE id IN ({_ph(visible)}))", tuple(visible)


def _id_cond(visible, col: str, allow_null: bool):
    if visible is None:
        return None, ()
    null = f"{col} IS NULL OR " if allow_null else ""
    return f"({null}{col} IN ({_ph(visible)}))", tuple(visible)


def _where(*conds) -> tuple[str, list]:
    parts, params = [], []
    for c, p in conds:
        if c:
            parts.append(c)
            params += list(p)
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def handle(spine, query: str, visible=None) -> str | None:
    """visible = skup vidljivih client_id (ili None za manager/nescopeano). Svi
    agregati se ograničavaju da restringirani radnik ne vidi tuđe brojeve/imena."""
    q = _normalize(query)

    if re.search(r"(koliko|broj)\s+(je\s+)?racuna\b", q):
        period = _period(q)
        pc = ("issued LIKE ?", (period + "%",)) if period else (None, ())
        where, params = _where(pc, _oib_cond(visible))
        row = spine.read().execute(f"SELECT COUNT(*) AS n FROM eracuni{where}", params).fetchone()
        return f"Broj računa: {row['n']}."

    if re.search(r"(zbroj|ukupno).*pdv", q):
        period = _period(q)
        pc = ("issued LIKE ?", (period + "%",)) if period else (None, ())
        where, params = _where(pc, _oib_cond(visible))
        row = spine.read().execute(f"SELECT SUM(vat) AS s FROM eracuni{where}", params).fetchone()
        return f"Ukupni PDV: {(row['s'] or 0):g}."

    m = re.search(r"top\s*(\d+)\s*klijen", q)
    if m:
        n = int(m.group(1))
        where, params = _where(_id_cond(visible, "c.id", allow_null=False))
        rows = spine.read().execute(
            "SELECT c.name AS name, COUNT(*) AS cnt FROM interactions i "
            f"JOIN clients c ON c.name = i.user{where} GROUP BY c.name ORDER BY cnt DESC LIMIT ?",
            (*params, n),
        ).fetchall()
        if not rows:
            return "Nema podataka o klijentima."
        parts = ", ".join(f"{r['name']} ({r['cnt']})" for r in rows)
        return f"Top {n} klijenata: {parts}."

    if re.search(r"(koliko|broj)\s+(je\s+)?dokumenata\b", q):
        where, params = _where(_id_cond(visible, "client_id", allow_null=True))
        row = spine.read().execute(f"SELECT COUNT(*) AS n FROM documents{where}", params).fetchone()
        return f"Broj dokumenata: {row['n']}."

    if re.search(r"(koliko|broj)\s+(je\s+)?klijenata\b", q):
        where, params = _where(_id_cond(visible, "id", allow_null=False))
        row = spine.read().execute(f"SELECT COUNT(*) AS n FROM clients{where}", params).fetchone()
        return f"Broj klijenata: {row['n']}."

    return None


from ragspine.rag import pipeline  # noqa: E402  (lazy: avoid any import-order coupling)
pipeline.LANE_HANDLERS["sql"] = lambda spine, cfg, query, llm, visible=None: handle(spine, query, visible=visible)
