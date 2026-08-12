# Aggregate numeric overview for the home page.

from datetime import date, timedelta

from atlas.business import expiry, deadline_calendar, obligations, peer_compare


def _today() -> date:
    return date.today()


def _urgency(due_str: str, today: date) -> str:
    """past due -> bad; soon (<=7 days) -> warn; else -> ok.
    Matches business/karton.py._urgency + .sdd/ui-DESIGN.md (soon(<=7d))."""
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
    upcoming AND recently-missed ones (deadline_calendar.upcoming() only looks forward,
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


def _month_events(spine, today: date, visible=None) -> dict:
    """Every dated obligation-deadline + document-expiry that falls in the
    current month, pinned to its day-of-month with an urgency state. Feeds the
    calendar hero (GET /dashboard.json -> calendar)."""
    start, end = _month_bounds(today)
    events: list[dict] = []

    def _add(iso: str, kind: str, label: str) -> None:
        # ponytail: expiry.expires is user-supplied and may be malformed; a bad
        # value must skip its event, never 500 the whole dashboard.
        try:
            day = date.fromisoformat(iso).day
        except (TypeError, ValueError):
            return
        events.append({"day": day, "kind": kind, "label": label, "state": _urgency(iso, today)})

    for r in spine.read().execute(
        "SELECT kind, due FROM deadline_dates WHERE due >= ? AND due < ? ORDER BY due",
        (start, end),
    ).fetchall():
        _add(r["due"], r["kind"], r["kind"])
    for r in spine.read().execute(
        "SELECT client_id, label, expires FROM expiry_items "
        "WHERE expires >= ? AND expires < ? ORDER BY expires",
        (start, end),
    ).fetchall():
        if visible is not None and r["client_id"] not in visible:
            continue  # a hidden client does not leak into the calendar
        _add(r["expires"], "istek", r["label"])
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
    kinds = obligations.active_kinds(spine)
    if not kinds:
        return []
    for kind in kinds:
        obligations.ensure_period(spine, kind, period)
    placeholders = ",".join("?" * len(kinds))
    rows = spine.read().execute(
        f"""SELECT o.client_id AS client_id, c.name AS client, o.kind AS kind
            FROM obligations o
            JOIN clients c ON c.id = o.client_id
            LEFT JOIN obligation_status s ON s.obligation_id = o.id
            WHERE o.period = ? AND o.kind IN ({placeholders}) AND COALESCE(s.sent, 0) = 0
            ORDER BY c.name COLLATE NOCASE""",
        (period, *kinds),
    ).fetchall()
    return [dict(r) for r in rows]


def stats(spine, visible=None) -> dict:
    active_clients = spine.read().execute(
        "SELECT COUNT(*) AS n FROM clients WHERE active=1"
    ).fetchone()["n"]
    deadlines_this_week = len(deadline_calendar.upcoming(spine, days=7))
    # ponytail: interactions has no client_id (no client attribution), so we
    # compute "top clients" by note count - the only available signal.
    # Upgrade path: add clients.id attribution to interactions when needed.
    top_all = spine.read().execute(
        """SELECT c.id AS id, c.name AS name, COUNT(*) AS cnt FROM notes n
           JOIN clients c ON c.id = n.client_id
           GROUP BY c.id ORDER BY cnt DESC, c.name"""
    ).fetchall()
    # a restricted worker does not see the names of hidden clients (Codex finding)
    top_rows = [r for r in top_all if visible is None or r["id"] in visible][:5]
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


def _vis(rows: list, visible) -> list:
    """Keep only rows of visible clients (client_id IS NULL = office-level, stays)."""
    if visible is None:
        return rows
    return [r for r in rows if r.get("client_id") is None or r.get("client_id") in visible]


def home_data(spine, cap: int = 8, visible=None) -> dict:
    """Aggregated payload for the dashboard screen (GET /dashboard.json).
    `visible` = set of visible client_id or None (all) - a restricted worker does
    not see obligations/expiry/missing-documents of other clients."""
    from atlas.business import doc_completeness
    today = _today()
    st = stats(spine, visible=visible)

    deadline_rows = _deadlines_window(spine, today)
    deadlines = [_with_state(r, "due", today) for r in deadline_rows][:cap]

    period = today.strftime("%Y-%m")
    unsent_full = _vis(_unsent_obligations(spine, period), visible)
    unsent = unsent_full[:cap]

    calendar = _month_events(spine, today, visible=visible)
    groups_full = _group_unsent(unsent_full, _kind_state(calendar["events"]))
    unsent_by_client = groups_full[:cap]

    missing_docs_full = doc_completeness.clients_missing_docs(spine, visible=visible)
    missing_docs = missing_docs_full[:cap]

    expiring_rows = _vis([dict(r) for r in expiry.expiring(spine, days=30)], visible)
    expiring = [_with_state(r, "expires", today) for r in expiring_rows][:cap]

    # over-fetch then filter by visibility before the cap - otherwise hidden rows
    # push visible ones below the LIMIT, or leak (Codex finding)
    notif_rows = spine.read().execute(
        """SELECT id, kind, body, client_id, seen, at FROM notifications
           WHERE seen=0 ORDER BY at DESC LIMIT ?""",
        (cap * 20,),
    ).fetchall()
    notifications = _vis([dict(r) for r in notif_rows], visible)[:cap]

    folders_rows = spine.read().execute(
        "SELECT f.id, f.label, f.role, f.path, s.n_subdirs, s.n_docs, s.n_pdf_no_text "
        "FROM folders f LEFT JOIN folder_scan s ON s.folder_id=f.id "
        "WHERE f.enabled=1 ORDER BY f.role, f.label COLLATE NOCASE").fetchall()
    orientation = {"folders": [
        {"id": r["id"], "label": r["label"] or r["path"], "role": r["role"],
         "scan": {"n_subdirs": r["n_subdirs"], "n_docs": r["n_docs"],
                  "n_pdf_no_text": r["n_pdf_no_text"]}}
        for r in folders_rows]}

    return {
        "orientation": orientation,
        "stats": st,
        "calendar": calendar,
        "deadlines": deadlines,
        "unsent_obligations": unsent,
        "unsent_by_client": unsent_by_client,
        # uncapped totals for the KPI numbers (rows above are capped for display)
        "unsent_total": len(unsent_full),
        "unsent_clients_total": len(groups_full),
        "expiring_total": len(expiring_rows),
        "expiring": expiring,
        "missing_docs": missing_docs,
        "missing_docs_total": len(missing_docs_full),
        "notifications": notifications,
        "peer": {"count": st["peer_disagreements"]},
    }
