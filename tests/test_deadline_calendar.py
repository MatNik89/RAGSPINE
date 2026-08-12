from datetime import date

from fastapi.testclient import TestClient

from atlas.business import deadline_calendar
from atlas.web.api import create_app
from atlas.web.deps import add_user


def test_expand_monthly():
    dates = deadline_calendar.expand("monthly:20", 2026)
    assert len(dates) == 12
    assert all(d.endswith("-20") for d in dates)
    assert [d[5:7] for d in dates] == [f"{m:02d}" for m in range(1, 13)]


def test_expand_yearly():
    assert deadline_calendar.expand("yearly:02-28", 2026) == ["2026-02-28"]


def test_expand_quarterly():
    dates = deadline_calendar.expand("quarterly:20", 2026)
    assert len(dates) == 4
    assert [d[5:7] for d in dates] == ["03", "06", "09", "12"]
    assert all(d.endswith("-20") for d in dates)


def test_rules_has_12_kinds():
    assert len(deadline_calendar.RULES) == 12
    assert len({r["kind"] for r in deadline_calendar.RULES}) == 12


def test_seed_and_upcoming(spine, monkeypatch):
    count = deadline_calendar.seed(spine, 2026)
    assert count > 0
    monkeypatch.setattr(deadline_calendar, "_today", lambda: date(2026, 2, 25))
    rows = deadline_calendar.upcoming(spine, days=14)
    assert any(r["kind"] == "TZ" and r["due"] == "2026-02-15" for r in rows) is False
    kinds_due = [(r["kind"], r["due"]) for r in rows]
    assert ("DOH", "2026-02-28") in kinds_due
    assert all(r["description"] for r in rows)


def test_seed_idempotent(spine):
    deadline_calendar.seed(spine, 2026)
    count2 = deadline_calendar.seed(spine, 2026)
    assert count2 == 0
    rows = spine.read().execute("SELECT COUNT(*) FROM deadline_dates").fetchone()[0]
    assert rows == sum(len(deadline_calendar.expand(r["rule"], 2026)) for r in deadline_calendar.RULES)


def test_api_kalendar_requires_auth(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    r = c.get("/kalendar")
    assert r.status_code == 401


def test_api_kalendar_json(spine, cfg, monkeypatch):
    deadline_calendar.seed(spine, 2026)
    monkeypatch.setattr(deadline_calendar, "_today", lambda: date(2026, 2, 25))
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.get("/kalendar?days=14", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert any(row["kind"] == "DOH" for row in r.json())
