from datetime import date, timedelta

from fastapi.testclient import TestClient

from atlas.business import dashboard, kalendar, expiry as expiry_mod
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    complete_setup(spine)
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _seed_client(spine, name="Alfa", oib="11111111111"):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO clients(name, oib, pdv_status, active) VALUES (?,?,'u sustavu pdv',1)",
            (name, oib),
        )
        return cur.lastrowid


# ---------- page ----------

def test_dashboard_page_authed_has_tiles_and_cards(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/", headers=_auth(tok))
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    text = r.text
    # KPI tiles
    assert "Aktivni klijenti" in text
    assert "Rokovi ovaj tjedan" in text
    assert "Neposlane obveze" in text
    assert "Nove obavijesti" in text
    # calendar-first hero + rail + board
    assert "Ured danas" in text
    assert "Što danas moram" in text
    assert 'id="cal-grid"' in text
    assert "obavijesti" in text.lower()
    # fetches the JSON endpoint client-side
    assert "/dashboard.json" in text
    # uses the shared shell (nav + fonts)
    assert "Nadzorna ploča" in text
    assert "@font-face" in text


def test_dashboard_page_no_auth_redirects(spine, cfg):
    add_user(spine, "_o", "pw")
    complete_setup(spine)
    c = _client(spine, cfg)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_dashboard_page_no_external_assets(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/", headers=_auth(tok))
    assert "http://" not in r.text
    assert "https://" not in r.text


def test_dashboard_page_renders_via_textcontent_not_innerhtml(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/", headers=_auth(tok))
    assert "innerHTML" not in r.text
    assert "textContent" in r.text


# ---------- /dashboard.json ----------

def test_dashboard_json_no_auth_401(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/dashboard.json")
    assert r.status_code == 401


def test_dashboard_json_has_expected_keys(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"orientation", "stats", "calendar", "deadlines", "unsent_obligations",
                          "unsent_by_client", "unsent_total", "unsent_clients_total",
                          "expiring", "expiring_total", "missing_docs", "missing_docs_total",
                          "notifications", "peer"}
    # calendar hero payload shape
    cal = body["calendar"]
    assert set(cal) == {"year", "month", "today", "events"}
    assert isinstance(cal["events"], list)


def test_dashboard_json_seeded_data_and_urgency(spine, cfg, monkeypatch):
    today = date(2026, 7, 10)
    monkeypatch.setattr(kalendar, "_today", lambda: today)
    monkeypatch.setattr(expiry_mod, "_today", lambda: today)
    monkeypatch.setattr(dashboard, "_today", lambda: today)

    cid = _seed_client(spine, "Alfa")
    period = today.strftime("%Y-%m")

    # unsent PDV obligation for Alfa this period
    from atlas.business import obveze
    obveze.ensure_period(spine, "PDV", period)

    # past-due deadline
    with spine.write() as c:
        c.execute("INSERT INTO deadlines(kind, rule, description) VALUES('X','monthly:1','Test rok')")
        c.execute(
            "INSERT INTO deadline_dates(kind, due, year) VALUES('X', ?, 2026)",
            ((today - timedelta(days=1)).isoformat(),),
        )
        c.execute("INSERT INTO deadlines(kind, rule, description) VALUES('Y','monthly:1','Uskoro rok')")
        c.execute(
            "INSERT INTO deadline_dates(kind, due, year) VALUES('Y', ?, 2026)",
            ((today + timedelta(days=2)).isoformat(),),
        )
        c.execute("INSERT INTO deadlines(kind, rule, description) VALUES('Z','monthly:1','Dalek rok')")
        c.execute(
            "INSERT INTO deadline_dates(kind, due, year) VALUES('Z', ?, 2026)",
            ((today + timedelta(days=6)).isoformat(),),
        )

    expiry_mod.add(spine, cid, "osobna", "Osobna iskaznica", (today + timedelta(days=2)).isoformat())

    with spine.write() as c:
        c.execute("INSERT INTO notifications(kind, body, seen) VALUES ('law_change', 'Nova stopa PDV-a', 0)")

    tok = _token(c := _client(spine, cfg), spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()

    # unsent obligations: Alfa PDV present
    assert any(u["client"] == "Alfa" and u["kind"] == "PDV" for u in body["unsent_obligations"])

    # deadline urgency states + days_left (the page JS chip renders from this —
    # a missing days_left shows literally as "za undefined d." on screen)
    by_kind = {d["kind"]: d for d in body["deadlines"]}
    assert by_kind["X"]["state"] == "bad" and by_kind["X"]["days_left"] == -1   # yesterday
    assert by_kind["Y"]["state"] == "warn" and by_kind["Y"]["days_left"] == 2   # in 2 days
    assert by_kind["Z"]["state"] == "warn" and by_kind["Z"]["days_left"] == 6  # in 6 days, now <=7 -> warn

    # expiring doc urgency (2 days -> warn) + days_left
    assert body["expiring"][0]["state"] == "warn"
    assert body["expiring"][0]["days_left"] == 2

    # notifications
    assert any("PDV-a" in n["body"] for n in body["notifications"])

    assert body["peer"]["count"] == 0
    assert isinstance(body["stats"]["active_clients"], int)


def test_urgency_thresholds():
    today = date(2026, 7, 10)
    assert dashboard._urgency((today - timedelta(days=1)).isoformat(), today) == "bad"
    assert dashboard._urgency((today + timedelta(days=2)).isoformat(), today) == "warn"
    assert dashboard._urgency((today + timedelta(days=3)).isoformat(), today) == "warn"
    assert dashboard._urgency((today + timedelta(days=7)).isoformat(), today) == "warn"
    assert dashboard._urgency((today + timedelta(days=8)).isoformat(), today) == "ok"
    assert dashboard._urgency((today + timedelta(days=10)).isoformat(), today) == "ok"
    assert dashboard._urgency(today.isoformat(), today) == "warn"


def test_dashboard_json_xss_safe_client_name(spine, cfg):
    _seed_client(spine, name="<script>alert(1)</script>", oib="22222222222")
    from atlas.business import obveze
    period = date.today().strftime("%Y-%m")
    obveze.ensure_period(spine, "PDV", period)

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    assert r.status_code == 200
    # JSON-safe: raw string present (not HTML-escaped) since this is application/json
    assert any(u["client"] == "<script>alert(1)</script>" for u in r.json()["unsent_obligations"])
    assert "application/json" in r.headers["content-type"]


def test_dashboard_calendar_events_pinned_to_day(spine, cfg, monkeypatch):
    today = date(2026, 8, 2)
    monkeypatch.setattr(kalendar, "_today", lambda: today)
    monkeypatch.setattr(expiry_mod, "_today", lambda: today)
    monkeypatch.setattr(dashboard, "_today", lambda: today)
    with spine.write() as c:
        c.execute("INSERT INTO deadlines(kind, rule, description) VALUES('PDV','monthly:20','PDV obrazac')")
        c.execute("INSERT INTO deadline_dates(kind, due, year) VALUES('PDV','2026-08-20',2026)")
        # a deadline in a DIFFERENT month must not leak in
        c.execute("INSERT INTO deadline_dates(kind, due, year) VALUES('PDV','2026-09-20',2026)")

    tok = _token(c := _client(spine, cfg), spine)
    cal = c.get("/dashboard.json", headers=_auth(tok)).json()["calendar"]
    assert cal["year"] == 2026 and cal["month"] == 8 and cal["today"] == 2
    days = [e["day"] for e in cal["events"]]
    assert 20 in days and 20 == [e for e in cal["events"] if e["kind"] == "PDV"][0]["day"]
    assert all(e["day"] != 20 or e["kind"] != "PDV" or e["state"] == "ok" for e in cal["events"])
    # September deadline excluded
    assert len(cal["events"]) == 1


def test_dashboard_unsent_grouped_by_client(spine, cfg):
    cid = _seed_client(spine, "Alfa")
    with spine.write() as c:  # employer -> JOPPD/DOH also apply
        c.execute("UPDATE clients SET has_employees=1 WHERE id=?", (cid,))

    tok = _token(c := _client(spine, cfg), spine)
    body = c.get("/dashboard.json", headers=_auth(tok)).json()
    groups = body["unsent_by_client"]
    alfa = [g for g in groups if g["client"] == "Alfa"]
    assert len(alfa) == 1  # one row per client, not per obligation
    kinds = {k["kind"] for k in alfa[0]["kinds"]}
    # PDV (pdv obligor) + JOPPD (has employees) — the active tab types — on one row.
    # DOH is a yearly/regime type and inactive by default, so it doesn't appear here.
    assert kinds == {"PDV", "JOPPD"}
    assert alfa[0]["client_id"] == cid


def test_dashboard_unsent_no_employees_only_pdv(spine, cfg):
    # a pdv-registered client with NO employees owes only PDV (JOPPD/DOH gate on employees)
    _seed_client(spine, "Beta", oib="33333333333")
    tok = _token(c := _client(spine, cfg), spine)
    body = c.get("/dashboard.json", headers=_auth(tok)).json()
    beta = [g for g in body["unsent_by_client"] if g["client"] == "Beta"]
    assert len(beta) == 1
    assert {k["kind"] for k in beta[0]["kinds"]} == {"PDV"}


def test_dashboard_kpi_totals_uncapped(spine, cfg, monkeypatch):
    today = date(2026, 7, 10)
    monkeypatch.setattr(kalendar, "_today", lambda: today)
    monkeypatch.setattr(dashboard, "_today", lambda: today)
    for i in range(12):  # 12 PDV obligors -> rows capped at 8, total must stay 12
        _seed_client(spine, name=f"K{i}", oib=str(10000000000 + i))
    tok = _token(c := _client(spine, cfg), spine)
    body = c.get("/dashboard.json", headers=_auth(tok)).json()
    assert len(body["unsent_obligations"]) <= 8
    assert body["unsent_total"] == 12
    assert body["unsent_clients_total"] == 12


def test_dashboard_survives_malformed_expiry_date(spine, cfg, monkeypatch):
    today = date(2026, 8, 2)
    monkeypatch.setattr(dashboard, "_today", lambda: today)
    cid = _seed_client(spine, "Alfa")
    with spine.write() as c:  # /expiry accepts arbitrary strings -> guard the dashboard
        c.execute("INSERT INTO expiry_items(client_id,kind,label,expires) VALUES(?,?,?,?)",
                  (cid, "x", "Loš datum", "2026-08-xx"))
    tok = _token(c := _client(spine, cfg), spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    assert r.status_code == 200  # ne 500


def test_dashboard_json_lists_are_capped(spine, cfg, monkeypatch):
    today = date(2026, 7, 10)
    monkeypatch.setattr(kalendar, "_today", lambda: today)
    for i in range(12):
        _seed_client(spine, name=f"Klijent{i}", oib=str(10000000000 + i))
    from atlas.business import obveze
    period = today.strftime("%Y-%m")
    obveze.ensure_period(spine, "PDV", period)

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    body = r.json()
    assert len(body["unsent_obligations"]) <= 8


def test_shell_uses_left_sidebar():
    from atlas.web.templates_ui import page_shell
    html = page_shell("Test", "<p>x</p>", active="home")
    assert 'class="sidebar"' in html
    assert '<main' in html
    assert 'ATLAS' in html
    assert 'aria-current' in html or 'class="active"' in html


def test_dashboard_has_ocr_action_js():
    from atlas.web.templates_ui import dashboard_page
    html = dashboard_page()
    assert "/folders/" in html and "/ocr" in html and "OCR-aj mapu" in html


def test_dashboard_orientation_empty_state_links_to_mape():
    """Wizard više ne postavlja mape (stranica mapa uklonjena) — prazno
    stanje 'Spoji mapu...' mora nuditi klikabilan put do /ui/mape."""
    from atlas.web.templates_ui import dashboard_page
    html = dashboard_page()
    assert "href = '/ui/mape'" in html
