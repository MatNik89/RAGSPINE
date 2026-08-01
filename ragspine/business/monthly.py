# Mjesečni pregled — "što sve moram ovaj mjesec" agregator preko
# kalendara, obveza, isteka dokumenata, promjena propisa i bilješki.

import re
from datetime import date

from ragspine.business import expiry, obveze

MONTHLY_RE = re.compile(
    r"[sš]to (sve )?(mi )?(jo[šs] )?moram|obaveze ovaj mjesec|mjese[cč]ni pregled|[sš]to moram ovaj mjesec",
    re.IGNORECASE,
)


def _period_now() -> str:
    return date.today().strftime("%Y-%m")


def _period_bounds(period: str) -> tuple[str, str]:
    """(start, end) ISO dates for `period` (YYYY-MM): first of the month and
    first of the NEXT month (exclusive end — handles Dec->Jan and short months)."""
    year, month = (int(x) for x in period.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def overview(spine, period: str) -> dict:
    start, end = _period_bounds(period)

    # ponytail: query deadline_dates directly for the requested period instead
    # of kalendar.upcoming() — upcoming() is anchored to today's 31-day window,
    # which is wrong for an arbitrary ?period= that isn't the current month.
    deadline_rows = spine.read().execute(
        """SELECT dd.id, dd.kind, dd.due, dd.year, d.description
           FROM deadline_dates dd JOIN deadlines d ON d.kind = dd.kind
           WHERE dd.due >= ? AND dd.due < ?
           ORDER BY dd.due""",
        (start, end),
    ).fetchall()
    deadlines = [dict(r) for r in deadline_rows]

    unsent = []
    for kind in obveze.KINDS:
        obveze.ensure_period(spine, kind, period)
        for row in obveze.list_period(spine, kind, period):
            if not row["sent"]:
                unsent.append({**row, "kind": kind})

    expiring = [dict(r) for r in expiry.expiring(spine, days=45)]

    watch_rows = spine.read().execute(
        """SELECT id, kind, body, client_id, seen, at FROM notifications
           WHERE kind IN ('law_change','rss') AND at >= ? AND at < ?
           ORDER BY at DESC""",
        (start, end),
    ).fetchall()
    watch_changes = [dict(r) for r in watch_rows]

    notes_rows = spine.read().execute(
        """SELECT n.id, n.client_id, n.author, n.body, n.created_at, c.name
           FROM notes n JOIN clients c ON c.id = n.client_id
           ORDER BY n.created_at DESC LIMIT 10"""
    ).fetchall()
    recent_notes = [dict(r) for r in notes_rows]

    return {
        "period": period,
        "deadlines": deadlines,
        "unsent": unsent,
        "expiring": expiring,
        "watch_changes": watch_changes,
        "recent_notes": recent_notes,
    }


def format_overview(overview: dict) -> str:
    period = overview["period"]
    unsent, deadlines = overview["unsent"], overview["deadlines"]
    expiring, watch, notes = overview["expiring"], overview["watch_changes"], overview["recent_notes"]

    lines = [f"Ovaj mjesec ({period}):"]
    if unsent:
        names = ", ".join(f"{r['client']} ({r['kind']})" for r in unsent)
        lines.append(f"- {len(unsent)} neposlanih obveza: {names}")
    else:
        lines.append("- sve obveze poslane")
    if deadlines:
        names = ", ".join(f"{d['kind']} {d['due']}" for d in deadlines)
        lines.append(f"- {len(deadlines)} rokova: {names}")
    else:
        lines.append("- nema rokova ovaj mjesec")
    lines.append(f"- {len(expiring)} isteka dokumenata u sljedećih 45 dana")
    lines.append(f"- {len(watch)} promjena propisa ovaj mjesec")
    lines.append(f"- {len(notes)} zadnjih bilješki")
    return "\n".join(lines)
