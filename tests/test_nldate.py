from datetime import date

from atlas.business import nldate

_NOW = date(2026, 8, 5)  # Wednesday


def _now():
    return _NOW


def test_relative_days():
    assert nldate.parse_date("za 10 dana", now_fn=_now) == "2026-08-15"
    assert nldate.parse_date("sutra", now_fn=_now) == "2026-08-06"
    assert nldate.parse_date("danas", now_fn=_now) == "2026-08-05"
    assert nldate.parse_date("prekosutra", now_fn=_now) == "2026-08-07"


def test_weekday_next_occurrence():
    assert nldate.parse_date("u petak", now_fn=_now) == "2026-08-07"
    # today IS Wednesday -> must roll to next week, not return today
    assert nldate.parse_date("u srijedu", now_fn=_now) == "2026-08-12"


def test_dot_dates():
    assert nldate.parse_date("do 15.9.", now_fn=_now) == "2026-09-15"
    # Jan 1 already passed this year -> rolls to next year
    assert nldate.parse_date("do 1.1.", now_fn=_now) == "2027-01-01"
    assert nldate.parse_date("15.09.2026", now_fn=_now) == "2026-09-15"


def test_garbage_returns_none():
    assert nldate.parse_date("blabla nista", now_fn=_now) is None


def test_diacritic_insensitive_weekday():
    assert nldate.parse_date("u četvrtak", now_fn=_now) == nldate.parse_date(
        "u cetvrtak", now_fn=_now
    )
    assert nldate.parse_date("u cetvrtak", now_fn=_now) == "2026-08-06"


def test_set_reminder_nl_inserts(spine):
    result = nldate.set_reminder_nl(spine, "ana", "Plati PDV", "za 5 dana", now_fn=_now)
    assert result["due"] == "2026-08-10"
    row = spine.read().execute(
        "SELECT user, body, due, done FROM reminders WHERE id=?", (result["id"],)
    ).fetchone()
    assert row["user"] == "ana"
    assert row["body"] == "Plati PDV"
    assert row["due"] == "2026-08-10"
    assert row["done"] == 0


def test_set_reminder_nl_unparseable_errors_and_does_not_insert(spine):
    before = spine.read().execute("SELECT COUNT(*) AS n FROM reminders").fetchone()["n"]
    result = nldate.set_reminder_nl(spine, "ana", "Nesto", "besmislica", now_fn=_now)
    assert result == {"error": "Ne razumijem datum"}
    after = spine.read().execute("SELECT COUNT(*) AS n FROM reminders").fetchone()["n"]
    assert after == before
