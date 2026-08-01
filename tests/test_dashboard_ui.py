from datetime import date, timedelta

from fastapi.testclient import TestClient

from ragspine.business import dashboard, kalendar, expiry as expiry_mod
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
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
    # stat tiles
    assert "Aktivni klijenti" in text
    assert "Rokovi ovaj tjedan" in text
    assert "Neposlane obveze" in text
    assert "Nepročitane obavijesti" in text
    # card titles
    assert "Rokovi" in text
    assert "Istek" in text
    assert "obavijesti" in text.lower()
    # fetches the JSON endpoint client-side
    assert "/dashboard.json" in text
    # uses the shared shell (nav + fonts)
    assert "Nadzorna ploča" in text
    assert "@font-face" in text


def test_dashboard_page_no_auth_redirects(spine, cfg):
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
    assert set(body) == {"stats", "deadlines", "unsent_obligations", "expiring",
                          "notifications", "peer"}


def test_dashboard_json_seeded_data_and_urgency(spine, cfg, monkeypatch):
    today = date(2026, 7, 10)
    monkeypatch.setattr(kalendar, "_today", lambda: today)
    monkeypatch.setattr(expiry_mod, "_today", lambda: today)
    monkeypatch.setattr(dashboard, "_today", lambda: today)

    cid = _seed_client(spine, "Alfa")
    period = today.strftime("%Y-%m")

    # unsent PDV obligation for Alfa this period
    from ragspine.business import obveze
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

    # deadline urgency states
    by_kind = {d["kind"]: d for d in body["deadlines"]}
    assert by_kind["X"]["state"] == "bad"      # yesterday
    assert by_kind["Y"]["state"] == "warn"     # in 2 days
    assert by_kind["Z"]["state"] == "ok"       # in 6 days

    # expiring doc urgency (2 days -> warn)
    assert body["expiring"][0]["state"] == "warn"

    # notifications
    assert any("PDV-a" in n["body"] for n in body["notifications"])

    assert body["peer"]["count"] == 0
    assert isinstance(body["stats"]["active_clients"], int)


def test_urgency_thresholds():
    today = date(2026, 7, 10)
    assert dashboard._urgency((today - timedelta(days=1)).isoformat(), today) == "bad"
    assert dashboard._urgency((today + timedelta(days=2)).isoformat(), today) == "warn"
    assert dashboard._urgency((today + timedelta(days=3)).isoformat(), today) == "warn"
    assert dashboard._urgency((today + timedelta(days=10)).isoformat(), today) == "ok"
    assert dashboard._urgency(today.isoformat(), today) == "warn"


def test_dashboard_json_xss_safe_client_name(spine, cfg):
    _seed_client(spine, name="<script>alert(1)</script>", oib="22222222222")
    from ragspine.business import obveze
    period = date.today().strftime("%Y-%m")
    obveze.ensure_period(spine, "PDV", period)

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    assert r.status_code == 200
    # JSON-safe: raw string present (not HTML-escaped) since this is application/json
    assert any(u["client"] == "<script>alert(1)</script>" for u in r.json()["unsent_obligations"])
    assert "application/json" in r.headers["content-type"]


def test_dashboard_json_lists_are_capped(spine, cfg, monkeypatch):
    today = date(2026, 7, 10)
    monkeypatch.setattr(kalendar, "_today", lambda: today)
    for i in range(12):
        _seed_client(spine, name=f"Klijent{i}", oib=str(10000000000 + i))
    from ragspine.business import obveze
    period = today.strftime("%Y-%m")
    obveze.ensure_period(spine, "PDV", period)

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    body = r.json()
    assert len(body["unsent_obligations"]) <= 8
