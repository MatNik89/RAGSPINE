# Materialize deadlines in advance + shift to a workday (weekend/holiday).
#
# Deadlines are generated from the type registry (obligation_types.rule) for a
# horizon of months ahead and written into deadline_dates (which the calendar
# hero reads). If a deadline falls on a Saturday/Sunday/holiday, it is shifted to
# the first following workday — the Croatian rule for tax deadlines.

from datetime import date, timedelta

from atlas.business import obveze

# Fixed Croatian public holidays (month, day). Easter/Easter Monday/Corpus
# Christi are movable (computed from Easter).
_FIXED_HOLIDAYS = [
    (1, 1),    # New Year's Day
    (1, 6),    # Epiphany
    (5, 1),    # Labour Day
    (5, 30),   # Statehood Day
    (6, 22),   # Anti-Fascist Struggle Day
    (8, 5),    # Victory and Homeland Thanksgiving Day
    (8, 15),   # Assumption of Mary
    (11, 1),   # All Saints' Day
    (11, 18),  # Remembrance Day for the Victims of the Homeland War
    (12, 25),  # Christmas
    (12, 26),  # St. Stephen's Day
]

_holiday_cache: dict[int, set] = {}


def _easter(year: int) -> date:
    """Easter Sunday (Gregorian, anonymous Meeus algorithm)."""
    a = year % 19
    b, c = year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (a + 11 * h + 22 * ((32 + 2 * e + 2 * i - h - k) % 7)) // 451
    lday = (32 + 2 * e + 2 * i - h - k) % 7
    month = (h + lday - 7 * m + 114) // 31
    day = ((h + lday - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def holidays(year: int) -> set:
    """Set of public holidays for the year (fixed + movable)."""
    cached = _holiday_cache.get(year)
    if cached is not None:
        return cached
    hs = {date(year, mm, dd) for mm, dd in _FIXED_HOLIDAYS}
    e = _easter(year)
    hs.add(e)                       # Easter
    hs.add(e + timedelta(days=1))   # Easter Monday
    hs.add(e + timedelta(days=60))  # Corpus Christi
    _holiday_cache[year] = hs
    return hs


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_workday(d: date) -> date:
    while not is_workday(d):
        d += timedelta(days=1)
    return d


def _last_day(year: int, month: int) -> int:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, _last_day(year, month)))


def due_for_month(rule: str, year: int, month: int) -> date | None:
    """The base (unshifted) deadline for a rule in the given month, or None if
    the rule does not generate one for that month."""
    if not rule or ":" not in rule:
        return None
    freq, spec = rule.split(":", 1)
    if freq == "monthly":
        try:
            return _clamp(year, month, int(spec))
        except ValueError:
            return None
    if freq == "quarterly":
        if month not in obveze._QUARTER_MONTHS:
            return None
        try:
            return _clamp(year, month, int(spec))
        except ValueError:
            return None
    if freq == "yearly":
        mm, _, dd = spec.partition("-")
        try:
            if month != int(mm):
                return None
            return _clamp(year, month, int(dd))
        except ValueError:
            return None
    return None


def _today() -> date:
    return date.today()


def generate(spine, months_ahead: int = 12, today: date | None = None) -> int:
    """Materializes deadline_dates for all registered types from the current
    month forward, with a shift to a workday. Idempotent (dedupe by kind+due).
    Returns the number of newly inserted dates."""
    today = today or _today()
    types = obveze.list_types(spine)
    month_start = date(today.year, today.month, 1).isoformat()
    added = 0
    with spine.write() as c:
        # catalog: every type with a rule must have a deadlines row (for the description JOIN)
        for t in types:
            if t["rule"]:
                c.execute(
                    "INSERT OR IGNORE INTO deadlines(kind, rule, description) VALUES(?,?,?)",
                    (t["kind"], t["rule"], t["label"]),
                )
                # Reconcile: delete FUTURE dates of this type (from the 1st of the
                # current month) and then regenerate them. Prevents duplicate/stale
                # deadlines when a legacy seed writes an unshifted date or the rule
                # changes. Past dates remain as history.
                c.execute(
                    "DELETE FROM deadline_dates WHERE kind=? AND due>=?",
                    (t["kind"], month_start),
                )
        base = today.year * 12 + (today.month - 1)
        for i in range(months_ahead + 1):
            idx = base + i
            yy, mm = idx // 12, idx % 12 + 1
            for t in types:
                due = due_for_month(t["rule"], yy, mm)
                if due is None:
                    continue
                shifted = next_workday(due).isoformat()
                exists = c.execute(
                    "SELECT 1 FROM deadline_dates WHERE kind=? AND due=?",
                    (t["kind"], shifted),
                ).fetchone()
                if exists is None:
                    c.execute(
                        "INSERT INTO deadline_dates(kind, due, year) VALUES(?,?,?)",
                        (t["kind"], shifted, yy),
                    )
                    added += 1
    return added
