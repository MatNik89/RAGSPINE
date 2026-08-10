"""Sigurnosni sweep B: endpointi koji su curili skrivene klijente restringiranom
radniku (sees_all_clients=0). /checklist /monthly /dashboard /watchlist/sources /sop."""
from fastapi.testclient import TestClient

from atlas.business import client_visibility, tenancy
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _restricted_worker(spine, cfg):
    """Vrati (client, headers, vis_id, hid_id): radnik vidi SAMO 'Vidljiv'."""
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    c.post("/auth/login", json={"username": "gazda", "password": "pw"})  # owner bootstrap org
    with spine.write() as conn:
        vis = conn.execute("INSERT INTO clients(name, active) VALUES('Vidljiv',1)").lastrowid
        hid = conn.execute("INSERT INTO clients(name, active) VALUES('Skriven',1)").lastrowid
    add_user(spine, "radnik", "pw")
    tok = c.post("/auth/login", json={"username": "radnik", "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username='radnik'").fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, "member")
    with spine.write() as conn:
        conn.execute("UPDATE users SET sees_all_clients=0 WHERE id=?", (uid,))
    client_visibility.grant(spine, uid, vis, "gazda")  # samo Vidljiv
    return c, {"Authorization": f"Bearer {tok}"}, vis, hid


def test_checklist_hides_invisible_client(spine, cfg):
    c, h, vis, hid = _restricted_worker(spine, cfg)
    names = [r["client"] for r in c.get("/checklist", headers=h).json()]
    assert "Vidljiv" in names and "Skriven" not in names


def test_dashboard_stats_top_clients_scoped(spine, cfg):
    from atlas.business import notes
    c, h, vis, hid = _restricted_worker(spine, cfg)
    notes.add(spine, hid, "a", "tajna")  # top_clients signal za skrivenog
    top = c.get("/dashboard", headers=h).json()["top_clients"]
    assert all(row[0] != "Skriven" for row in top)


def test_monthly_hides_invisible_client_notes(spine, cfg):
    from atlas.business import notes
    c, h, vis, hid = _restricted_worker(spine, cfg)
    notes.add(spine, hid, "a", "tajna biljeska")
    notes.add(spine, vis, "a", "vidljiva biljeska")
    ov = c.get("/monthly", headers=h).json()
    clients = [n.get("client") for n in ov.get("recent_notes", [])]
    assert "Skriven" not in clients


def test_watchlist_sources_hides_client_source(spine, cfg):
    c, h, vis, hid = _restricted_worker(spine, cfg)
    with spine.write() as conn:
        conn.execute("INSERT INTO watch_sources(url, client_id) VALUES('http://x', ?)", (hid,))
        conn.execute("INSERT INTO watch_sources(url, client_id) VALUES('http://zakon', NULL)")
    urls = [r["url"] for r in c.get("/watchlist/sources", headers=h).json()]
    assert "http://zakon" in urls and "http://x" not in urls


def test_sop_client_tied_guarded(spine, cfg):
    c, h, vis, hid = _restricted_worker(spine, cfg)
    with spine.write() as conn:
        sid = conn.execute("INSERT INTO sop_pages(title, client_id, status) "
                           "VALUES('Tajni SOP', ?, 'submitted')", (hid,)).lastrowid
    assert c.get(f"/sop/{sid}", headers=h).status_code == 403  # klijent nevidljiv
    pend = c.get("/sop/pending", headers=h).json()["items"]
    assert all(it.get("client_id") != hid for it in pend)
