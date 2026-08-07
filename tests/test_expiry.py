from datetime import date, timedelta

from fastapi.testclient import TestClient

from atlas.business import expiry
from atlas.web.api import create_app
from atlas.web.deps import add_user


def _client(spine):
    with spine.write() as c:
        cur = c.execute("INSERT INTO clients(name, oib) VALUES ('Alfa', '1')")
    return cur.lastrowid


def test_add_and_expiring(spine, monkeypatch):
    cid = _client(spine)
    today = date(2026, 1, 1)
    monkeypatch.setattr(expiry, "_today", lambda: today)
    expires = (today + timedelta(days=30)).isoformat()
    item_id = expiry.add(spine, cid, "osobna", "Osobna iskaznica", expires)
    assert isinstance(item_id, int)

    rows60 = expiry.expiring(spine, days=60)
    assert any(r["id"] == item_id and r["client_name"] == "Alfa" for r in rows60)

    rows10 = expiry.expiring(spine, days=10)
    assert all(r["id"] != item_id for r in rows10)


def test_api_expiry_post_and_get(spine, cfg):
    cid = _client(spine)
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}
    expires = (date.today() + timedelta(days=10)).isoformat()
    r = c.post("/expiry", json={"client_id": cid, "kind": "osobna", "label": "Osobna", "expires": expires},
               headers=headers)
    assert r.status_code == 200
    item_id = r.json()["id"]
    r2 = c.get("/expiry?days=60", headers=headers)
    assert r2.status_code == 200
    assert any(row["id"] == item_id for row in r2.json())
