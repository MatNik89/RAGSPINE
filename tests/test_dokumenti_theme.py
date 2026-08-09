"""Tema A: fali-dokumenata na info panelu + mail na istek dokumenta."""
from atlas.business import doc_completeness, expiry


def _client(spine, name="Pekara"):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name) VALUES(?)", (name,)).lastrowid


def _require(spine, cid, *keys):
    with spine.write() as c:
        for k in keys:
            c.execute("INSERT INTO client_doc_types(client_id, doc_type_key) VALUES(?,?)", (cid, k))


def _has_doc(spine, cid, doc_type):
    with spine.write() as c:
        c.execute("INSERT INTO documents(title, client_id, doc_type) VALUES('d.pdf',?,?)", (cid, doc_type))


# --- A2: fali dokumenata --------------------------------------------------

def test_missing_for_client(spine):
    cid = _client(spine)
    _require(spine, cid, "ugovor", "osobna_iskaznica")
    _has_doc(spine, cid, "ugovor")
    assert doc_completeness.missing_for_client(spine, cid) == ["osobna_iskaznica"]


def test_clients_missing_docs_aggregate(spine):
    c1 = _client(spine, "Alfa"); c2 = _client(spine, "Beta")
    _require(spine, c1, "ugovor"); _require(spine, c2, "ugovor")
    _has_doc(spine, c2, "ugovor")  # Beta ima sve
    out = doc_completeness.clients_missing_docs(spine)
    names = {r["client"]: r for r in out}
    assert "Alfa" in names and "Beta" not in names  # samo oni kojima fali
    assert names["Alfa"]["nedostaju"] == ["ugovor"]


def test_clients_missing_docs_visibility_scoped(spine):
    c1 = _client(spine, "Vidljiv"); c2 = _client(spine, "Skriven")
    _require(spine, c1, "ugovor"); _require(spine, c2, "ugovor")
    out = doc_completeness.clients_missing_docs(spine, visible={c1})
    assert [r["client"] for r in out] == ["Vidljiv"]


def test_home_data_includes_missing_docs(spine):
    from atlas.business import dashboard
    cid = _client(spine)
    _require(spine, cid, "ugovor")
    data = dashboard.home_data(spine)
    assert "missing_docs" in data
    assert any(r["client"] == "Pekara" for r in data["missing_docs"])


def test_home_data_visibility_covers_calendar_stats_notifications(spine):
    # Codex: /dashboard.json ne smije procuriti skrivenog klijenta ni kroz
    # kalendar/stats/notifications, ne samo kroz expiring/unsent
    from atlas.business import dashboard, expiry, notes
    from datetime import date
    vis = _client(spine, "Vidljiv"); hid = _client(spine, "Skriven")
    soon = date.today().replace(day=15).isoformat()
    expiry.add(spine, hid, "osobna", "Skriven ugovor", soon)  # u kalendaru ovog mjeseca
    notes.add(spine, hid, "a", "bilješka")  # top_clients signal
    with spine.write() as c:
        c.execute("INSERT INTO notifications(kind, body, client_id) VALUES('x','tajna',?)", (hid,))
    data = dashboard.home_data(spine, visible={vis})
    labels = [e["label"] for e in data["calendar"]["events"]]
    assert "Skriven ugovor" not in labels
    assert all("Skriven" != n for n, _ in data["stats"]["top_clients"])
    assert all(n.get("client_id") != hid for n in data["notifications"])


def test_ntfy_target_dropped_from_allowlist():
    from atlas.web import messaging
    assert not messaging._target_scheme_ok("ntfy://127.0.0.1/topic")  # SSRF vektor izbačen
    assert messaging._target_scheme_ok("mailto://x@example.com")  # fiksni host ostaje


# --- A1: mail na istek ----------------------------------------------------

def test_send_expiry_reminder_no_consent_skipped(spine, cfg):
    from atlas.web import messaging  # noqa: F401 (osigurava import path)
    cid = _client(spine)
    eid = expiry.add(spine, cid, "osobna_iskaznica", "Osobna", "2026-09-01")
    res = expiry.send_expiry_reminder(spine, cfg, eid)
    assert res["status"] == "skipped_no_consent"  # bez pristanka -> ne šalje


def test_send_expiry_reminder_composes_subject(spine, cfg):
    cid = _client(spine)
    with spine.write() as c:
        c.execute("UPDATE clients SET messaging_consent=1, messaging_channel='mail', "
                  "messaging_target='mailto://x@example.com' WHERE id=?", (cid,))
    eid = expiry.add(spine, cid, "osobna_iskaznica", "Osobna iskaznica", "2026-09-01")
    res = expiry.send_expiry_reminder(spine, cfg, eid, dry_run=True)
    assert res["status"] in ("dry_run", "sent")
    row = spine.read().execute("SELECT subject FROM message_log WHERE client_id=?", (cid,)).fetchone()
    assert "Osobna iskaznica" in row["subject"] or "ističe" in row["subject"].lower()


def test_send_expiry_reminder_unknown_item(spine, cfg):
    import pytest
    with pytest.raises(ValueError):
        expiry.send_expiry_reminder(spine, cfg, 9999)


def test_expiry_reminder_endpoint(spine, cfg):
    from fastapi.testclient import TestClient

    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    cid = _client(spine)
    eid = expiry.add(spine, cid, "osobna_iskaznica", "Osobna", "2026-09-01")
    c = TestClient(create_app(spine, cfg))
    assert c.post(f"/expiry/{eid}/podsjetnik").status_code in (401, 403)
    add_user(spine, "ana", "pw"); complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    r = c.post(f"/expiry/{eid}/podsjetnik", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and "status" in r.json()
