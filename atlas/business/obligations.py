# Monthly obligations per client — a data-driven registry of types (obligation_types).
#
# Obligation types (PDV, JOPPD, rent, ...) live in the obligation_types table, not in
# code. A worker adds/edits them through the UI; this module only reads the registry
# and for each type resolves WHO is the obligor (applies_to) then creates obligations rows.

# Built-in default types — seeded lazily (INSERT OR IGNORE) so admin edits
# persist. (kind, label, rule, frequency, applies_to, sort, active)
import json
from datetime import date
# active=0 means: exists in the registry, but is not a tab until a worker enables it.
DEFAULT_TYPES = [
    # (kind, label, rule, frequency, applies_to, sort, active, category)
    # PDV: from 2026-01-01 the filing deadline = the LAST day of the month (VAT Act
    # amendments; it was the 20th) — monthly:31 is clamped to the month end in due_for_month.
    ("PDV", "PDV", "monthly:31", "monthly", "pdv", 10, 1, ""),
    # JOPPD = payroll; label "Plaće" + category 'place' so AI/UI recognize payroll.
    ("JOPPD", "Plaće", "monthly:15", "monthly", "employees", 20, 1, "place"),
    ("DOH", "Prijava poreza na dohodak (DOH)", "yearly:02-28", "yearly", "dohodak", 30, 0, ""),
    ("PO-SD", "Paušalno izvješće (PO-SD)", "yearly:01-15", "yearly", "pausal", 40, 0, ""),
    ("PD", "Prijava poreza na dobit (PD)", "yearly:04-30", "yearly", "dobit", 50, 0, ""),
]

# Back-compat: some modules/tests import obligations.KINDS. The registry is the source
# of truth; these are just the default kinds.
KINDS = tuple(t[0] for t in DEFAULT_TYPES)

# Who is the obligor. Besides the simple ones (pdv/employees/all_active/manual) — the
# client's tax regime: DOH=income-tax payer, PO-SD=flat-rate, PD/GFI=profit-tax payer.
_REGIME_APPLIES = ("dobit", "dohodak", "pausal")
APPLIES_TO = ("pdv", "employees", "all_active", "manual", *_REGIME_APPLIES)
REGIMES = ("", *_REGIME_APPLIES)
FREQUENCIES = ("monthly", "quarterly", "yearly")
# Months in which a quarterly obligor files (after the quarter ends).
_QUARTER_MONTHS = (1, 4, 7, 10)


def _yearly_month(rule: str) -> int:
    """Deadline month for a yearly type from the rule 'yearly:MM-DD'. Default January."""
    try:
        return int((rule or "").split(":", 1)[1][:2])
    except (IndexError, ValueError):
        return 1


def _ensure_seeded(spine) -> None:
    with spine.write() as c:
        for kind, label, rule, freq, applies, sort, active, category in DEFAULT_TYPES:
            c.execute(
                """INSERT OR IGNORE INTO obligation_types
                   (kind, label, rule, frequency, applies_to, active, sort, category)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (kind, label, rule, freq, applies, active, sort, category),
            )


_TYPE_COLS = "kind, label, rule, frequency, applies_to, active, sort, description, category"


def list_types(spine, active_only: bool = False) -> list[dict]:
    _ensure_seeded(spine)
    q = f"SELECT {_TYPE_COLS} FROM obligation_types"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY sort, kind"
    return [dict(r) for r in spine.read().execute(q).fetchall()]


def get_type(spine, kind: str) -> dict | None:
    _ensure_seeded(spine)
    r = spine.read().execute(
        f"SELECT {_TYPE_COLS} FROM obligation_types WHERE kind=?", (kind,)
    ).fetchone()
    return dict(r) if r else None


def active_kinds(spine) -> list[str]:
    return [t["kind"] for t in list_types(spine, active_only=True)]


def upsert_type(spine, kind: str, label: str, rule: str, frequency: str,
                applies_to: str, active: bool = True, sort: int = 100,
                description: str = "", category: str | None = None, user: str = "?") -> str:
    """Create or edit an obligation type. kind is normalized to UPPERCASE
    (stable key). Returns the normalized kind."""
    kind = (kind or "").strip().upper()
    if not kind:
        raise ValueError("kind je obavezan")
    if frequency not in FREQUENCIES:
        raise ValueError(f"nepoznata frekvencija: {frequency!r}")
    if applies_to not in APPLIES_TO:
        raise ValueError(f"nepoznat applies_to: {applies_to!r}")
    rule = (rule or "").strip()
    if rule:
        rf, _, arg = rule.partition(":")
        if rf not in FREQUENCIES:
            raise ValueError(f"nepravilno pravilo roka: {rule!r}")
        if rf != frequency:
            raise ValueError(f"pravilo ({rf}) ne odgovara frekvenciji ({frequency})")
        # also validate the RANGE/format of arg, not just the prefix — otherwise an active
        # type silently ends up without deadlines (Codex finding)
        if rf in ("monthly", "quarterly"):
            if not (arg.isdigit() and 1 <= int(arg) <= 31):
                raise ValueError(f"dan roka mora biti 1–31: {rule!r}")
        else:  # yearly:MM-DD
            try:
                date.fromisoformat(f"2024-{arg}")  # 2024 = leap year, allows 02-29
            except ValueError:
                raise ValueError(f"godišnji rok mora biti MM-DD: {rule!r}") from None
    label = (label or "").strip() or kind
    _ensure_seeded(spine)
    with spine.write() as c:
        # category None = "do not touch" (new row -> '', existing -> keep);
        # an explicit string sets it. Otherwise an upsert without category would erase
        # the existing one (e.g. JOPPD 'place') — Codex finding.
        cat = None if category is None else category.strip()
        c.execute(
            """INSERT INTO obligation_types
                 (kind, label, rule, frequency, applies_to, active, sort, description, category)
               VALUES(?,?,?,?,?,?,?,?,COALESCE(?, ''))
               ON CONFLICT(kind) DO UPDATE SET
                 label=excluded.label, rule=excluded.rule, frequency=excluded.frequency,
                 applies_to=excluded.applies_to, active=excluded.active,
                 sort=excluded.sort, description=excluded.description,
                 category=CASE WHEN ? IS NULL THEN obligation_types.category ELSE ? END""",
            (kind, label, rule, frequency, applies_to, int(bool(active)), sort, description,
             cat, cat, cat),
        )
    spine.audit(user, "obligation_type_upsert", f"type:{kind}")
    return kind


def set_client_types(spine, client_id: int, kinds: list[str], user: str = "?") -> None:
    """Replaces the set of 'manual' types a client has (e.g. rent)."""
    with spine.write() as c:
        c.execute("DELETE FROM client_obligation_types WHERE client_id=?", (client_id,))
        for k in kinds:
            c.execute(
                "INSERT OR IGNORE INTO client_obligation_types(client_id, kind) VALUES(?,?)",
                (client_id, k),
            )
    spine.audit(user, "client_obligation_types_set", f"client:{client_id}")


def client_types(spine, client_id: int) -> list[str]:
    return [r["kind"] for r in spine.read().execute(
        "SELECT kind FROM client_obligation_types WHERE client_id=?", (client_id,)
    ).fetchall()]


def _obligor_ids(spine, otype: dict, period: str) -> list[int]:
    """Clients that have an obligation for this type and period, per the applies_to rule."""
    applies = otype["applies_to"]
    month = int(period[5:7])
    c = spine.read()

    # The type-frequency gate applies to ALL types (incl. custom pdv+yearly):
    # a quarterly/yearly type exists only in the filing month.
    if otype["frequency"] == "quarterly" and month not in _QUARTER_MONTHS:
        return []
    if otype["frequency"] == "yearly" and month != _yearly_month(otype["rule"]):
        return []

    if applies == "pdv":
        # "u sustavu PDV-a" YES, but NOT "nije u sustavu PDV-a" (LIKE '%u sustavu%'
        # would otherwise also catch the negative value).
        rows = c.execute(
            "SELECT id, pdv_freq FROM clients WHERE active=1 "
            "AND lower(pdv_status) LIKE '%u sustavu%' AND lower(pdv_status) NOT LIKE '%nije%'"
        ).fetchall()
        ids = []
        for r in rows:
            # per-client frequency: a quarterly obligor only in quarterly months
            if (r["pdv_freq"] or "monthly") == "quarterly" and month not in _QUARTER_MONTHS:
                continue
            ids.append(r["id"])
        return ids

    if applies == "employees":
        return [r["id"] for r in c.execute(
            "SELECT id FROM clients WHERE active=1 AND has_employees=1").fetchall()]
    if applies in _REGIME_APPLIES:
        return [r["id"] for r in c.execute(
            "SELECT id FROM clients WHERE active=1 AND regime=?", (applies,)).fetchall()]
    if applies == "all_active":
        return [r["id"] for r in c.execute(
            "SELECT id FROM clients WHERE active=1").fetchall()]
    if applies == "manual":
        return [r["id"] for r in c.execute(
            """SELECT c.id FROM clients c
               JOIN client_obligation_types t ON t.client_id = c.id
               WHERE c.active=1 AND t.kind=?""", (otype["kind"],)).fetchall()]
    return []


def ensure_period(spine, kind: str, period: str) -> None:
    """Creates an obligations row for each obligor of that type in that period.
    Idempotent (INSERT OR IGNORE + UNIQUE(client_id,kind,period))."""
    otype = get_type(spine, kind)
    if otype is None:
        raise ValueError(f"Nepoznat kind obveze: {kind!r}")
    ids = _obligor_ids(spine, otype, period)
    id_set = set(ids)
    with spine.write() as c:
        for cid in ids:
            c.execute(
                "INSERT OR IGNORE INTO obligations(client_id, kind, period) VALUES(?,?,?)",
                (cid, kind, period),
            )
        # Remove stale UNSENT obligations (the client is no longer an obligor — lost
        # employees, changed regime, became inactive, manual removed...). Sent ones
        # (sent=1) we keep as history.
        stale = c.execute(
            """SELECT o.id, o.client_id FROM obligations o
               LEFT JOIN obligation_status s ON s.obligation_id = o.id
               WHERE o.kind=? AND o.period=? AND COALESCE(s.sent, 0) = 0""",
            (kind, period),
        ).fetchall()
        for r in stale:
            if r["client_id"] not in id_set:
                c.execute("DELETE FROM obligation_status WHERE obligation_id=?", (r["id"],))
                c.execute("DELETE FROM obligations WHERE id=?", (r["id"],))


def list_period(spine, kind: str, period: str) -> list[dict]:
    rows = spine.read().execute(
        """SELECT o.id AS obligation_id, o.client_id AS client_id, c.name AS client,
                  COALESCE(s.sent, 0) AS sent, s.sent_by AS sent_by, s.sent_at AS sent_at,
                  o.meta AS meta
           FROM obligations o
           JOIN clients c ON c.id = o.client_id
           LEFT JOIN obligation_status s ON s.obligation_id = o.id
           WHERE o.kind = ? AND o.period = ?
           ORDER BY sent, c.name COLLATE NOCASE""",
        (kind, period),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
        except (ValueError, TypeError):
            d["meta"] = {}
        out.append(d)
    return out


def worker_activity(spine, since: str | None = None) -> list[dict]:
    """Who closed how many obligations + the last time. From obligation_status (sent=1).
    The data already exists (sent_by/sent_at) — this is an aggregation for the report."""
    q = ("SELECT s.sent_by AS worker, COUNT(*) AS closed, MAX(s.sent_at) AS last_at "
         "FROM obligation_status s JOIN obligations o ON o.id = s.obligation_id "
         "WHERE s.sent=1 AND s.sent_by IS NOT NULL")
    args: tuple = ()
    if since:
        q += " AND s.sent_at >= ?"
        args = (since,)
    q += " GROUP BY s.sent_by ORDER BY closed DESC, worker COLLATE NOCASE"
    return [dict(r) for r in spine.read().execute(q, args).fetchall()]


def worker_closed(spine, worker: str, since: str | None = None) -> list[dict]:
    """Individual obligations that this worker closed (for drill-down)."""
    q = ("SELECT o.kind AS kind, o.period AS period, c.name AS client, s.sent_at AS sent_at "
         "FROM obligation_status s JOIN obligations o ON o.id = s.obligation_id "
         "JOIN clients c ON c.id = o.client_id "
         "WHERE s.sent=1 AND s.sent_by=?")
    args: tuple = (worker,)
    if since:
        q += " AND s.sent_at >= ?"
        args = (worker, since)
    q += " ORDER BY s.sent_at DESC"
    return [dict(r) for r in spine.read().execute(q, args).fetchall()]


def mark_sent(spine, obligation_id: int, user: str, sent: bool = True) -> None:
    with spine.write() as c:
        if c.execute("SELECT 1 FROM obligations WHERE id=?", (obligation_id,)).fetchone() is None:
            raise ValueError(f"nepoznata obveza: {obligation_id}")
        c.execute(
            """INSERT INTO obligation_status(obligation_id, sent, sent_by, sent_at)
               VALUES(?,?,?,datetime('now'))
               ON CONFLICT(obligation_id) DO UPDATE SET
                 sent=excluded.sent, sent_by=excluded.sent_by, sent_at=excluded.sent_at""",
            (obligation_id, int(sent), user),
        )
    spine.audit(user, "obligation_sent" if sent else "obligation_unsent", f"obligation:{obligation_id}")
