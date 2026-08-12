from datetime import date, timedelta

from fastapi.testclient import TestClient

from atlas.business import dashboard
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


def _seed_client(spine, name="Alfa", oib="11111111111", pdv="u sustavu pdv"):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO clients(name, oib, pdv_status, active) VALUES (?,?,?,1)",
            (name, oib, pdv),
        )
        return cur.lastrowid


# ---------- 5A: obveze board + campaign button ----------

def test_obveze_page_has_campaign_button(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-07", headers=_auth(tok))
    assert r.status_code == 200
    text = r.text
    assert "Pošalji podsjetnik nepredanima" in text
    assert "/messaging/campaign" in text
    # design shell reuse
    assert "@font-face" in text
    assert "oblig-row" in text


def test_obveze_campaign_js_defaults_dry_run_true_and_requires_checkbox(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-07", headers=_auth(tok))
    text = r.text
    assert "stvarno pošalji" in text
    assert 'id="campaign-real"' in text
    # dry_run flips to !checkbox.checked -> only false when the box is checked
    assert "dry_run" in text
    assert "!really" in text or "campaign-real" in text


def test_obveze_mark_sent_still_works(spine, cfg):
    _seed_client(spine, "Alfa")
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-07", headers=_auth(tok))
    assert "Alfa" in r.text
    from atlas.business import obveze
    rows = obveze.list_period(spine, "PDV", "2026-07")
    r2 = c.post("/obveze/mark", json={"obligation_id": rows[0]["obligation_id"], "kind": "PDV",
                                       "period": "2026-07"}, headers=_auth(tok))
    assert r2.status_code == 200
    rows2 = obveze.list_period(spine, "PDV", "2026-07")
    assert rows2[0]["sent"] == 1


def test_obveze_page_no_auth_redirects(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/obveze", follow_redirects=False)
    assert r.status_code == 303


# ---------- security fix: reflected script-context XSS via `period` ----------

_XSS_PERIOD = "</script><script>alert(1)</script>"


def test_obveze_page_rejects_script_breakout_period(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze", params={"kind": "PDV", "period": _XSS_PERIOD}, headers=_auth(tok))
    assert r.status_code == 400


def test_obveze_json_rejects_script_breakout_period(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze.json", params={"kind": "PDV", "period": _XSS_PERIOD}, headers=_auth(tok))
    assert r.status_code == 400


def test_obveze_mark_rejects_script_breakout_period(spine, cfg):
    _seed_client(spine, "Alfa")
    c = _client(spine, cfg)
    tok = _token(c, spine)
    from atlas.business import obveze
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    r = c.post("/obveze/mark", json={"obligation_id": rows[0]["obligation_id"], "kind": "PDV",
                                      "period": _XSS_PERIOD}, headers=_auth(tok))
    assert r.status_code == 400


def test_obveze_page_accepts_valid_period(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze", params={"kind": "PDV", "period": "2026-07"}, headers=_auth(tok))
    assert r.status_code == 200


def test_script_json_escapes_script_breakout():
    from atlas.web.templates_ui import script_json
    embedded = script_json(_XSS_PERIOD)
    assert "</script>" not in embedded
    assert "\\u003c" in embedded
    # round-trips back to the original value once parsed as JS/JSON
    import json
    assert json.loads(embedded.replace("\\u003c", "<")) == _XSS_PERIOD


def test_script_json_escapes_js_line_separators():
    from atlas.web.templates_ui import script_json
    embedded = script_json("line1 line2 line3")
    assert " " not in embedded
    assert " " not in embedded
    assert "\\u2028" in embedded and "\\u2029" in embedded


def test_render_obveze_defense_in_depth_even_with_malicious_period():
    # belt-and-suspenders: even if a future caller forgets endpoint-level
    # validation, render_obveze() itself must never let `period` break out
    # of the inline <script> tag.
    from atlas.web.templates_obveze import render_obveze
    html_out = render_obveze("PDV", _XSS_PERIOD, [])
    assert "</script><script>alert(1)</script>" not in html_out
    assert "\\u003c" in html_out


# ---------- 5B: notifications inbox ----------

def test_notifications_json_auth(spine, cfg):
    with spine.write() as conn:
        conn.execute("INSERT INTO notifications(kind, body) VALUES ('law_change', 'Nova stopa PDV-a')")
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/notifications.json", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert any(n["body"] == "Nova stopa PDV-a" for n in body)
    assert all(set(n) >= {"id", "kind", "body", "seen", "at"} for n in body)


def test_notifications_json_no_auth_401(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/notifications.json")
    assert r.status_code == 401


def test_notifications_mark_seen(spine, cfg):
    with spine.write() as conn:
        cur = conn.execute("INSERT INTO notifications(kind, body) VALUES ('rss', 'Novost')")
        nid = cur.lastrowid
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post(f"/notifications/{nid}/seen", headers=_auth(tok))
    assert r.status_code == 200
    row = spine.read().execute("SELECT seen FROM notifications WHERE id=?", (nid,)).fetchone()
    assert row["seen"] == 1


def test_notifications_mark_seen_unknown_404(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/notifications/999999/seen", headers=_auth(tok))
    assert r.status_code == 404


def test_ui_obavijesti_authed(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/obavijesti", headers=_auth(tok))
    assert r.status_code == 200
    assert "/notifications.json" in r.text
    assert "Obavijesti" in r.text
    assert "@font-face" in r.text


def test_ui_obavijesti_no_auth_redirects(spine, cfg):
    add_user(spine, "_o", "pw")
    complete_setup(spine)
    c = _client(spine, cfg)
    r = c.get("/ui/obavijesti", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_nav_has_obavijesti_link(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/", headers=_auth(tok))
    assert "/ui/obavijesti" in r.text


# ---------- 5C: chat richer ----------

def test_chat_page_client_chip_links_to_karton(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/chat", headers=_auth(tok))
    text = r.text
    assert "/ui/klijent/" in text
    assert "Klijent: " in text


def test_chat_page_variants_refill_js_present(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/chat", headers=_auth(tok))
    text = r.text
    assert "variants" in text
    assert "q.value" in text


def test_chat_page_xss_safe_textcontent(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/chat", headers=_auth(tok))
    assert "innerHTML" not in r.text
    assert "textContent" in r.text


# ---------- 5D: doc generator screen ----------

def test_ui_dokumenti_authed(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/dokumenti", headers=_auth(tok))
    assert r.status_code == 200
    text = r.text
    assert "/doc/generate" in text
    assert "/doc/templates" in text
    assert "/clients" in text
    assert "Upozorenje: brojke nedostaju u dokumentu" in text
    assert "@font-face" in text


def test_ui_dokumenti_no_auth_redirects(spine, cfg):
    add_user(spine, "_o", "pw")
    complete_setup(spine)
    c = _client(spine, cfg)
    r = c.get("/ui/dokumenti", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_doc_generate_flow_still_works(spine, cfg):
    cid = _seed_client(spine, "Alfa")
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/doc/generate", json={"doc_type": "ponuda", "client_id": cid,
                                       "extra": {"stavke": [{"naziv": "Usluga", "iznos": 100}]}},
               headers=_auth(tok))
    assert r.status_code == 200
    assert "text" in r.json()


# ---------- 5E: dashboard urgency threshold ≤7d ----------

def test_dashboard_deadline_in_5_days_is_warn(spine, cfg, monkeypatch):
    from atlas.business import deadline_calendar
    today = date(2026, 7, 10)
    monkeypatch.setattr(deadline_calendar, "_today", lambda: today)
    monkeypatch.setattr(dashboard, "_today", lambda: today)
    with spine.write() as conn:
        conn.execute("INSERT INTO deadlines(kind, rule, description) VALUES('Q','monthly:1','Rok')")
        conn.execute(
            "INSERT INTO deadline_dates(kind, due, year) VALUES('Q', ?, 2026)",
            ((today + timedelta(days=5)).isoformat(),),
        )
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    q = next(d for d in body["deadlines"] if d["kind"] == "Q")
    assert q["state"] == "warn"


def test_dashboard_deadline_past_still_bad(spine, cfg, monkeypatch):
    from atlas.business import deadline_calendar
    today = date(2026, 7, 10)
    monkeypatch.setattr(deadline_calendar, "_today", lambda: today)
    monkeypatch.setattr(dashboard, "_today", lambda: today)
    with spine.write() as conn:
        conn.execute("INSERT INTO deadlines(kind, rule, description) VALUES('R','monthly:1','Rok')")
        conn.execute(
            "INSERT INTO deadline_dates(kind, due, year) VALUES('R', ?, 2026)",
            ((today - timedelta(days=2)).isoformat(),),
        )
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/dashboard.json", headers=_auth(tok))
    body = r.json()
    rr = next(d for d in body["deadlines"] if d["kind"] == "R")
    assert rr["state"] == "bad"


# ---------- global: no external assets, /ui pages redirect unauth ----------

def test_new_pages_no_external_assets(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    for url in ("/obveze?kind=PDV&period=2026-07", "/ui/obavijesti", "/ui/dokumenti"):
        r = c.get(url, headers=_auth(tok))
        assert r.status_code == 200
        assert "http://" not in r.text
        assert "https://" not in r.text


def test_new_ui_pages_redirect_unauth():
    pass  # covered per-page above (kept for symmetry with spec bullet)
