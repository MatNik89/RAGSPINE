from fastapi.testclient import TestClient

from atlas.business import peer_compare
from atlas.web.api import create_app
from atlas.web.deps import add_user


def test_norm_groups_by_kind_numbers_and_dates():
    a = peer_compare._norm("Račun 55 od 1.7.2026")
    b = peer_compare._norm("Racun 88 od 3.8.2026")
    assert a == b == "racun # od DATE"


def test_norm_replaces_oib():
    assert peer_compare._norm("OIB klijenta 12345678901") == "oib klijenta OIB"


def test_record_booking_inserts_and_audits(spine):
    bid = peer_compare.record_booking(spine, "ana", "racun za gorivo 12", "4020")
    assert isinstance(bid, int) and bid > 0
    row = spine.read().execute("SELECT * FROM peer_bookings WHERE id=?", (bid,)).fetchone()
    assert row is not None
    assert row["user"] == "ana"
    assert row["konto"] == "4020"
    assert row["description_norm"] == peer_compare._norm("racun za gorivo 12")
    audit = spine.read().execute(
        "SELECT * FROM audit_log WHERE action='peer_booking'"
    ).fetchone()
    assert audit is not None and audit["user"] == "ana"


def test_find_disagreements_flags_two_users_two_kontos(spine):
    peer_compare.record_booking(spine, "ana", "reprezentacija restoran racun 12", "4010")
    peer_compare.record_booking(spine, "ivan", "reprezentacija restoran racun 45", "4014")

    groups = peer_compare.find_disagreements(spine)
    assert len(groups) == 1
    g = groups[0]
    assert g["description_norm"] == "reprezentacija restoran racun #"
    assert set(g["kontos"]) == {"4010", "4014"}
    assert g["kontos"]["4010"] == ["ana"]
    assert g["kontos"]["4014"] == ["ivan"]
    assert g["sample_description"] in (
        "reprezentacija restoran racun 12", "reprezentacija restoran racun 45",
    )


def test_find_disagreements_same_konto_is_no_disagreement(spine):
    peer_compare.record_booking(spine, "ana", "reprezentacija restoran racun 12", "4010")
    peer_compare.record_booking(spine, "ivan", "reprezentacija restoran racun 45", "4010")
    assert peer_compare.find_disagreements(spine) == []


def test_find_disagreements_single_user_is_no_disagreement(spine):
    peer_compare.record_booking(spine, "ana", "reprezentacija restoran racun 12", "4010")
    peer_compare.record_booking(spine, "ana", "reprezentacija restoran racun 45", "4014")
    assert peer_compare.find_disagreements(spine) == []


def _insert_at(spine, user, description, konto, at):
    with spine.write() as c:
        c.execute(
            "INSERT INTO peer_bookings(user, description, description_norm, konto, at) "
            "VALUES(?,?,?,?,?)",
            (user, description, peer_compare._norm(description), konto, at),
        )


def test_find_disagreements_respects_days_window(spine):
    _insert_at(spine, "ana", "reprezentacija restoran racun 12", "4010", "2020-01-01 00:00:00")
    _insert_at(spine, "ivan", "reprezentacija restoran racun 45", "4014", "2020-01-01 00:00:00")
    assert peer_compare.find_disagreements(spine, days=30) == []


def test_peer_summary_no_disagreements(spine):
    assert peer_compare.peer_summary(spine) == "Nema neslaganja u knjiženju."


def test_peer_summary_names_kontos(spine):
    peer_compare.record_booking(spine, "Ana", "reprezentacija restoran racun 12", "4010")
    peer_compare.record_booking(spine, "Ivan", "reprezentacija restoran racun 45", "4014")
    text = peer_compare.peer_summary(spine)
    assert "Neslaganje u knjiženju" in text
    assert "4010" in text and "4014" in text
    assert "Ana" in text and "Ivan" in text
    assert "Uskladiti" in text


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine, username="ana", password="tajna"):
    add_user(spine, username, password)
    return c.post("/auth/login", json={"username": username, "password": password}).json()["token"]


def test_api_peer_booking_then_disagreements(spine, cfg):
    c = _client(spine, cfg)
    tok_ana = _token(c, spine, "ana", "tajna")
    tok_ivan = _token(c, spine, "ivan", "tajna2")

    r = c.post("/peer/booking",
               json={"description": "reprezentacija restoran racun 12", "konto": "4010"},
               headers={"Authorization": f"Bearer {tok_ana}"})
    assert r.status_code == 200
    assert r.json()["id"]

    r2 = c.post("/peer/booking",
                json={"description": "reprezentacija restoran racun 45", "konto": "4014"},
                headers={"Authorization": f"Bearer {tok_ivan}"})
    assert r2.status_code == 200

    headers = {"Authorization": f"Bearer {tok_ana}"}

    r3 = c.get("/peer/disagreements?days=30", headers=headers)
    assert r3.status_code == 200
    body = r3.json()
    assert len(body["disagreements"]) == 1
    assert "Neslaganje" in body["summary"]


def test_api_peer_booking_requires_auth(spine, cfg):
    c = _client(spine, cfg)
    r = c.post("/peer/booking", json={"description": "x", "konto": "4010"},
               follow_redirects=False)
    assert r.status_code in (401, 303)
