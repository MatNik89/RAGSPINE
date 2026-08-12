# Tax calendar - 12 deadline types + mini-RRULE expansion into dates.
#
# ponytail: the rule DSL covers only monthly/yearly/quarterly (enough for all
# HR obligations below). Upgrade path: add "weekly"/"biweekly" if needed.

from datetime import date, timedelta

RULES: list[dict] = [
    {"kind": "PDV", "rule": "monthly:31", "description": "PDV obrazac do zadnjeg dana u mjesecu za prethodni (od 2026.)"},
    {"kind": "JOPPD", "rule": "monthly:15", "description": "JOPPD obrazac (pojednostavljeno: na dan isplate, rok 15. u mjesecu)"},
    {"kind": "DOH", "rule": "yearly:02-28", "description": "Prijava poreza na dohodak (DOH)"},
    {"kind": "PD", "rule": "yearly:04-30", "description": "Prijava poreza na dobit (PD)"},
    {"kind": "PDV-S", "rule": "monthly:31", "description": "Zbirna prijava PDV-S do zadnjeg dana u mjesecu (od 2026.)"},
    {"kind": "ZP", "rule": "monthly:31", "description": "Zbirna prijava ZP do zadnjeg dana u mjesecu (od 2026.)"},
    {"kind": "OPZ-STAT", "rule": "quarterly:20", "description": "OPZ-STAT statističko izvješće, kvartalno do 20."},
    {"kind": "TZ", "rule": "monthly:15", "description": "Turistička zajednica - mjesečna članarina do 15."},
    {"kind": "GFI", "rule": "yearly:04-30", "description": "Godišnji financijski izvještaj (GFI)"},
    {"kind": "SR", "rule": "yearly:02-28", "description": "Statistička renta (SR) godišnje izvješće"},
    {"kind": "turisticka_clanarina", "rule": "monthly:15", "description": "Turistička članarina do 15. u mjesecu"},
    {"kind": "spomenicka_renta", "rule": "monthly:15", "description": "Spomenička renta do 15. u mjesecu"},
]


def expand(rule: str, year: int) -> list[str]:
    freq, spec = rule.split(":", 1)
    if freq == "monthly":
        day = int(spec)
        return [f"{year:04d}-{m:02d}-{day:02d}" for m in range(1, 13)]
    if freq == "yearly":
        return [f"{year:04d}-{spec}"]
    if freq == "quarterly":
        day = int(spec)
        return [f"{year:04d}-{m:02d}-{day:02d}" for m in (3, 6, 9, 12)]
    raise ValueError(f"Nepoznato pravilo kalendara: {rule!r}")


def seed(spine, year: int) -> int:
    inserted = 0
    with spine.write() as c:
        for r in RULES:
            c.execute(
                "INSERT OR IGNORE INTO deadlines(kind, rule, description) VALUES(?,?,?)",
                (r["kind"], r["rule"], r["description"]),
            )
        for r in RULES:
            for due in expand(r["rule"], year):
                exists = c.execute(
                    "SELECT 1 FROM deadline_dates WHERE kind=? AND due=?", (r["kind"], due)
                ).fetchone()
                if exists is None:
                    c.execute(
                        "INSERT INTO deadline_dates(kind, due, year) VALUES(?,?,?)",
                        (r["kind"], due, year),
                    )
                    inserted += 1
    return inserted


def _today() -> date:
    return date.today()


def upcoming(spine, days: int = 14) -> list:
    today = _today()
    end = today + timedelta(days=days)
    return spine.read().execute(
        """SELECT dd.id, dd.kind, dd.due, dd.year, d.description
           FROM deadline_dates dd
           JOIN deadlines d ON d.kind = dd.kind
           WHERE dd.due BETWEEN ? AND ?
           ORDER BY dd.due""",
        (today.isoformat(), end.isoformat()),
    ).fetchall()
