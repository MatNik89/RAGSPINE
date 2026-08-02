# Materijalizacija rokova unaprijed + pomak na radni dan (vikend/blagdan).
#
# Rokovi se generiraju iz registra vrsta (obligation_types.rule) za horizont
# mjeseci unaprijed i upisuju u deadline_dates (koje čita kalendar-hero). Ako
# rok padne na subotu/nedjelju/blagdan, pomiče se na prvi sljedeći radni dan —
# hrvatsko pravilo za porezne rokove.

from datetime import date, timedelta

from ragspine.business import obveze

# Fiksni hrvatski neradni blagdani (mjesec, dan). Uskrs/Uskrsni ponedjeljak/
# Tijelovo su pomični (računaju se iz Uskrsa).
_FIXED_HOLIDAYS = [
    (1, 1),    # Nova godina
    (1, 6),    # Sveta tri kralja
    (5, 1),    # Praznik rada
    (5, 30),   # Dan državnosti
    (6, 22),   # Dan antifašističke borbe
    (8, 5),    # Dan pobjede i domovinske zahvalnosti
    (8, 15),   # Velika Gospa
    (11, 1),   # Svi sveti
    (11, 18),  # Dan sjećanja na žrtve Domovinskog rata
    (12, 25),  # Božić
    (12, 26),  # Sveti Stjepan
]

_holiday_cache: dict[int, set] = {}


def _easter(year: int) -> date:
    """Uskrsna nedjelja (gregorijanski, anonimni Meeus algoritam)."""
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
    """Set neradnih blagdana za godinu (fiksni + pomični)."""
    cached = _holiday_cache.get(year)
    if cached is not None:
        return cached
    hs = {date(year, mm, dd) for mm, dd in _FIXED_HOLIDAYS}
    e = _easter(year)
    hs.add(e)                       # Uskrs
    hs.add(e + timedelta(days=1))   # Uskrsni ponedjeljak
    hs.add(e + timedelta(days=60))  # Tijelovo
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
    """Osnovni (nepomaknuti) rok za pravilo u danom mjesecu, ili None ako ga
    pravilo za taj mjesec ne generira."""
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
    """Materijalizira deadline_dates za sve registrirane vrste od tekućeg mjeseca
    unaprijed, s pomakom na radni dan. Idempotentno (dedupe po kind+due).
    Vraća broj novoupisanih datuma."""
    today = today or _today()
    types = obveze.list_types(spine)
    added = 0
    with spine.write() as c:
        # katalog: svaka vrsta s pravilom mora imati deadlines red (za JOIN opisa)
        for t in types:
            if t["rule"]:
                c.execute(
                    "INSERT OR IGNORE INTO deadlines(kind, rule, description) VALUES(?,?,?)",
                    (t["kind"], t["rule"], t["label"]),
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
