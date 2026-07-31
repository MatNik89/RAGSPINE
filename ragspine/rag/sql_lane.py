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


def handle(spine, query: str) -> str | None:
    q = _normalize(query)

    if re.search(r"(koliko|broj)\s+(je\s+)?racuna\b", q):
        period = _period(q)
        if period:
            row = spine.read().execute(
                "SELECT COUNT(*) AS n FROM eracuni WHERE issued LIKE ?", (period + "%",)
            ).fetchone()
        else:
            row = spine.read().execute("SELECT COUNT(*) AS n FROM eracuni").fetchone()
        return f"Broj računa: {row['n']}."

    if re.search(r"(zbroj|ukupno).*pdv", q):
        period = _period(q)
        if period:
            row = spine.read().execute(
                "SELECT SUM(vat) AS s FROM eracuni WHERE issued LIKE ?", (period + "%",)
            ).fetchone()
        else:
            row = spine.read().execute("SELECT SUM(vat) AS s FROM eracuni").fetchone()
        total = row["s"] or 0
        return f"Ukupni PDV: {total:g}."

    m = re.search(r"top\s*(\d+)\s*klijen", q)
    if m:
        n = int(m.group(1))
        rows = spine.read().execute(
            "SELECT c.name AS name, COUNT(*) AS cnt FROM interactions i "
            "JOIN clients c ON c.name = i.user GROUP BY c.name ORDER BY cnt DESC LIMIT ?",
            (n,),
        ).fetchall()
        if not rows:
            return "Nema podataka o klijentima."
        parts = ", ".join(f"{r['name']} ({r['cnt']})" for r in rows)
        return f"Top {n} klijenata: {parts}."

    if re.search(r"(koliko|broj)\s+(je\s+)?dokumenata\b", q):
        row = spine.read().execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return f"Broj dokumenata: {row['n']}."

    if re.search(r"(koliko|broj)\s+(je\s+)?klijenata\b", q):
        row = spine.read().execute("SELECT COUNT(*) AS n FROM clients").fetchone()
        return f"Broj klijenata: {row['n']}."

    return None


from ragspine.rag import pipeline  # noqa: E402  (lazy: avoid any import-order coupling)
pipeline.LANE_HANDLERS["sql"] = lambda spine, cfg, query, llm: handle(spine, query)
