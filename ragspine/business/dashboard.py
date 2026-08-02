# Agregatni brojčani pregled za početnu stranicu.

from datetime import date, timedelta

from ragspine.business import expiry, kalendar, obveze, peer_compare


def _today() -> date:
    return date.today()


def _urgency(due_str: str, today: date) -> str:
    """kasni (past due) -> bad; uskoro (<=7 dana) -> warn; else -> ok.
    Matches business/karton.py._urgency + .sdd/ui-DESIGN.md (uskoro(<=7d))."""
    delta = (date.fromisoformat(due_str) - today).days
    if delta < 0:
        return "bad"
    if delta <= 7:
        return "warn"
    return "ok"


def _with_state(row: dict, date_field: str, today: date) -> dict:
    try:
        row["days_left"] = (date.fromisoformat(row[date_field]) - today).days
        row["state"] = _urgency(row[date_field], today)
    except (TypeError, ValueError):
        # ponytail: bad/missing date -> don't crash the dashboard, just show
        # it with no urgency signal. Upgrade path: surface a data-quality flag.
        row["days_left"] = None
        row["state"] = "ok"
    return row


def _deadlines_window(spine, today: date, back_days: int = 7, fwd_days: int = 7) -> list[dict]:
    """Deadlines due in [-back_days, +fwd_days] around today — surfaces both
    upcoming AND recently-missed ones (kalendar.upcoming() only looks forward,
    so a past-due deadline would otherwise never reach the dashboard)."""
    start = (today - timedelta(days=back_days)).isoformat()
    end = (today + timedelta(days=fwd_days)).isoformat()
    rows = spine.read().execute(
        """SELECT dd.id, dd.kind, dd.due, dd.year, d.description
           FROM deadline_dates dd JOIN deadlines d ON d.kind = dd.kind
           WHERE dd.due BETWEEN ? AND ?
           ORDER BY dd.due""",
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def _month_bounds(today: date) -> tuple[str, str]:
    """[first-of-month, first-of-next-month) as ISO strings (end exclusive)."""
    start = date(today.year, today.month, 1)
    nxt = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    return start.isoformat(), nxt.isoformat()


def _month_events(spine, today: date) -> dict:
    """Every dated obligation-deadline + document-expiry that falls in the
    current month, pinned to its day-of-month with an urgency state. Feeds the
    calendar hero (GET /dashboard.json -> calendar)."""
    start, end = _month_bounds(today)
    events: list[dict] = []
    for r in spine.read().execute(
        "SELECT kind, due FROM deadline_dates WHERE due >= ? AND due < ? ORDER BY due",
        (start, end),
    ).fetchall():
        events.append({"day": int(r["due"][8:10]), "kind": r["kind"],
                       "label": r["kind"], "state": _urgency(r["due"], today)})
    for r in spine.read().execute(
        "SELECT label, expires FROM expiry_items WHERE expires >= ? AND expires < ? ORDER BY expires",
        (start, end),
    ).fetchall():
        events.append({"day": int(r["expires"][8:10]), "kind": "istek",
                       "label": r["label"], "state": _urgency(r["expires"], today)})
    return {"year": today.year, "month": today.month, "today": today.day, "events": events}


_STATE_ORDER = {"bad": 0, "warn": 1, "ok": 2}


def _kind_state(events: list[dict]) -> dict:
    """kind -> worst urgency state seen in the month (bad < warn < ok). Used to
    colour the unsent-obligation chips by their deadline's real status."""
    out: dict[str, str] = {}
    for ev in events:
        k = ev["kind"]
        if k not in out or _STATE_ORDER[ev["state"]] < _STATE_ORDER[out[k]]:
            out[k] = ev["state"]
    return out


def _group_unsent(unsent: list[dict], kind_state: dict) -> list[dict]:
    """One row per client with its unsent obligation kinds as chips, instead of
    one row per obligation (kills the repetitive per-kind rows on the board)."""
    out: dict[int, dict] = {}
    for u in unsent:
        g = out.setdefault(u["client_id"],
                           {"client_id": u["client_id"], "client": u["client"], "kinds": []})
        # Everything here is outstanding, so a chip must never read as "done"
        # (green/ok): overdue -> bad, otherwise warn. Green is reserved for the
        # calendar, where it means a deadline that is genuinely still far off.
        state = "bad" if kind_state.get(u["kind"]) == "bad" else "warn"
        g["kinds"].append({"kind": u["kind"], "state": state})
    return list(out.values())


def _unsent_obligations(spine, period: str) -> list[dict]:
    for kind in obveze.KINDS:
        obveze.ensure_period(spine, kind, period)
    placeholders = ",".join("?" * len(obveze.KINDS))
    rows = spine.read().execute(
        f"""SELECT o.client_id AS client_id, c.name AS client, o.kind AS kind
            FROM obligations o
            JOIN clients c ON c.id = o.client_id
            LEFT JOIN obligation_status s ON s.obligation_id = o.id
            WHERE o.period = ? AND o.kind IN ({placeholders}) AND COALESCE(s.sent, 0) = 0
            ORDER BY c.name COLLATE NOCASE""",
        (period, *obveze.KINDS),
    ).fetchall()
    return [dict(r) for r in rows]


def stats(spine) -> dict:
    active_clients = spine.read().execute(
        "SELECT COUNT(*) AS n FROM clients WHERE active=1"
    ).fetchone()["n"]
    deadlines_this_week = len(kalendar.upcoming(spine, days=7))
    # ponytail: interactions nema client_id (nema atribucije klijentu), pa
    # "top klijenti" računamo po broju bilješki — jedini dostupan signal.
    # Upgrade path: dodati clients.id atribuciju na interactions kad zatreba.
    top_rows = spine.read().execute(
        """SELECT c.name AS name, COUNT(*) AS cnt FROM notes n
           JOIN clients c ON c.id = n.client_id
           GROUP BY c.id ORDER BY cnt DESC, c.name LIMIT 5"""
    ).fetchall()
    unseen_notifications = spine.read().execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE seen=0"
    ).fetchone()["n"]
    try:
        peer_disagreements = len(peer_compare.find_disagreements(spine))
    except Exception:
        peer_disagreements = 0
    return {
        "active_clients": active_clients,
        "deadlines_this_week": deadlines_this_week,
        "top_clients": [(r["name"], r["cnt"]) for r in top_rows],
        "unseen_notifications": unseen_notifications,
        "peer_disagreements": peer_disagreements,
    }


def home_data(spine, cap: int = 8) -> dict:
    """Aggregated payload for the dashboard screen (GET /dashboard.json)."""
    today = _today()
    st = stats(spine)

    deadline_rows = _deadlines_window(spine, today)
    deadlines = [_with_state(r, "due", today) for r in deadline_rows][:cap]

    period = today.strftime("%Y-%m")
    unsent_full = _unsent_obligations(spine, period)
    unsent = unsent_full[:cap]

    calendar = _month_events(spine, today)
    unsent_by_client = _group_unsent(unsent_full, _kind_state(calendar["events"]))[:cap]

    expiring_rows = [dict(r) for r in expiry.expiring(spine, days=30)]
    expiring = [_with_state(r, "expires", today) for r in expiring_rows][:cap]

    notif_rows = spine.read().execute(
        """SELECT id, kind, body, client_id, seen, at FROM notifications
           WHERE seen=0 ORDER BY at DESC LIMIT ?""",
        (cap,),
    ).fetchall()
    notifications = [dict(r) for r in notif_rows]

    return {
        "stats": st,
        "calendar": calendar,
        "deadlines": deadlines,
        "unsent_obligations": unsent,
        "unsent_by_client": unsent_by_client,
        "expiring": expiring,
        "notifications": notifications,
        "peer": {"count": st["peer_disagreements"]},
    }
