from datetime import date, timedelta

from fastapi.testclient import TestClient

from ragspine.business import expiry as expiry_mod
from ragspine.web import messaging
from ragspine.web.api import create_app
from ragspine.web.deps import add_user

VALID_OIB = "69435151530"  # validan testni OIB (kontrolna znamenka provjerena)


def _client(spine, name, oib, consent=0, channel="apprise", target="", active=1):
    with spine.write() as c:
        cur = c.execute(
            """INSERT INTO clients(name, oib, active, messaging_consent, messaging_channel, messaging_target)
               VALUES(?,?,?,?,?,?)""",
            (name, oib, active, consent, channel, target),
        )
    return cur.lastrowid


def _obligation(spine, client_id, kind, period, sent=0):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO obligations(client_id, kind, period) VALUES(?,?,?)",
            (client_id, kind, period),
        )
        oid = cur.lastrowid
        if sent:
            c.execute("INSERT INTO obligation_status(obligation_id, sent) VALUES(?,1)", (oid,))
    return oid


def test_send_to_client_no_consent_skips(spine, cfg):
    cid = _client(spine, "Bez pristanka", "1", consent=0)
    result = messaging.send_to_client(spine, cfg, cid, "Podsjetnik", "tekst poruke")
    assert result["status"] == "skipped_no_consent"
    rows = spine.read().execute("SELECT * FROM message_log WHERE client_id=?", (cid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped_no_consent"


def test_send_to_client_no_target_skips(spine, cfg):
    cid = _client(spine, "Bez cilja", "2", consent=1, target="")
    result = messaging.send_to_client(spine, cfg, cid, "Podsjetnik", "tekst poruke")
    assert result["status"] == "skipped_no_consent"


def test_send_to_client_dry_run_default_no_transmission(spine, cfg, monkeypatch):
    cid = _client(spine, "S pristankom", "3", consent=1, target="mailto://a@b.com")
    calls = []
    monkeypatch.setattr(messaging.optional, "need", lambda *a, **k: calls.append(a) or None)

    result = messaging.send_to_client(spine, cfg, cid, "Podsjetnik", "tekst poruke")

    assert result["status"] == "dry_run"
    assert calls == []  # apprise nikad pozvan u dry-run
    rows = spine.read().execute("SELECT * FROM message_log WHERE client_id=?", (cid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "dry_run"


def test_campaign_compliance_missing(spine, cfg):
    consented1 = _client(spine, "A", "10", consent=1, target="mailto://a")
    consented2 = _client(spine, "B", "11", consent=1, target="mailto://b")
    unconsented = _client(spine, "C", "12", consent=0)
    already_sent = _client(spine, "D", "13", consent=1, target="mailto://d")

    _obligation(spine, consented1, "PDV", "2026-08", sent=0)
    _obligation(spine, consented2, "PDV", "2026-08", sent=0)
    _obligation(spine, unconsented, "PDV", "2026-08", sent=0)
    _obligation(spine, already_sent, "PDV", "2026-08", sent=1)

    audience = messaging.build_audience(spine, "compliance_missing", kind="PDV", period="2026-08")
    assert set(audience) == {consented1, consented2, unconsented}

    result = messaging.send_to_filter(
        spine, cfg, "compliance_missing", "Nepodneseni PDV", "molimo dostavite",
        dry_run=True, kind="PDV", period="2026-08",
    )
    assert result["audience"] == 3
    assert result["results"]["dry_run"] == 2
    assert result["results"]["skipped_no_consent"] == 1


def test_expiring_soon_audience(spine, cfg, monkeypatch):
    today = date(2026, 8, 1)
    monkeypatch.setattr(expiry_mod, "_today", lambda: today)
    cid = _client(spine, "Ističe", "20", consent=1, target="mailto://e")
    expiry_mod.add(spine, cid, "osobna", "Osobna iskaznica", (today + timedelta(days=20)).isoformat())

    aud30 = messaging.build_audience(spine, "expiring_soon", days=30)
    aud10 = messaging.build_audience(spine, "expiring_soon", days=10)

    assert cid in aud30
    assert cid not in aud10


def test_message_log_body_preview_redacted_and_truncated(spine, cfg):
    cid = _client(spine, "Redakcija", "30", consent=0)
    body = f"Klijentov OIB je {VALID_OIB} i evo jos dosta teksta " + ("x" * 200)

    messaging.send_to_client(spine, cfg, cid, "Podsjetnik", body)

    row = spine.read().execute(
        "SELECT body_preview FROM message_log WHERE client_id=?", (cid,)
    ).fetchone()
    assert "[OIB]" in row["body_preview"]
    assert VALID_OIB not in row["body_preview"]
    assert len(row["body_preview"]) <= 120


def test_consent_endpoint_then_send_no_longer_skipped(spine, cfg):
    cid = _client(spine, "Naknadni pristanak", "40", consent=0)
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}

    r = c.post(
        f"/clients/{cid}/messaging",
        json={"consent": 1, "channel": "apprise", "target": "mailto://naknadni@b.com"},
        headers=headers,
    )
    assert r.status_code == 200

    result = messaging.send_to_client(spine, cfg, cid, "Podsjetnik", "tekst")
    assert result["status"] == "dry_run"


def test_consent_endpoint_rejects_invalid_consent_value(spine, cfg):
    cid = _client(spine, "Nevaljan", "41", consent=0)
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.post(
        f"/clients/{cid}/messaging",
        json={"consent": 2, "channel": "apprise", "target": "x"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400


def test_api_campaign_dry_run_returns_audience_count(spine, cfg):
    cid = _client(spine, "Kampanja", "50", consent=1, target="mailto://k@b.com")
    _obligation(spine, cid, "PDV", "2026-08", sent=0)

    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]

    r = c.post(
        "/messaging/campaign",
        json={"filter": "compliance_missing", "subject": "Nepodneseni PDV",
              "body": "molimo dostavite", "dry_run": True, "kind": "PDV", "period": "2026-08"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["audience"] == 1


def test_api_send_requires_consent(spine, cfg):
    cid = _client(spine, "Bez pristanka API", "60", consent=0)
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]

    r = c.post(
        "/messaging/send",
        json={"client_id": cid, "subject": "Podsjetnik", "body": "tekst"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "skipped_no_consent"


def test_api_messaging_log(spine, cfg):
    cid = _client(spine, "Log", "70", consent=0)
    messaging.send_to_client(spine, cfg, cid, "Podsjetnik", "tekst")
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]

    r = c.get(f"/messaging/log?client_id={cid}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "skipped_no_consent"
