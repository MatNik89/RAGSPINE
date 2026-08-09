"""Izvještaj radnik × zatvorene obveze (iz postojećih obligation_status/audit).
Podatak već postoji (sent_by/sent_at) — ovo je samo agregacija + prikaz."""
from fastapi.testclient import TestClient

from atlas.business import obveze, tenancy
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _seed_obligations(spine):
    with spine.write() as c:
        c1 = c.execute("INSERT INTO clients(name) VALUES('Pekara')").lastrowid
        c2 = c.execute("INSERT INTO clients(name) VALUES('Mlin')").lastrowid
        o1 = c.execute("INSERT INTO obligations(client_id,kind,period) VALUES(?,?,?)",
                       (c1, "PDV", "2026-08")).lastrowid
        o2 = c.execute("INSERT INTO obligations(client_id,kind,period) VALUES(?,?,?)",
                       (c2, "PDV", "2026-08")).lastrowid
        o3 = c.execute("INSERT INTO obligations(client_id,kind,period) VALUES(?,?,?)",
                       (c1, "JOPPD", "2026-08")).lastrowid
    return o1, o2, o3


def test_worker_activity_counts_closures_per_worker(spine):
    o1, o2, o3 = _seed_obligations(spine)
    obveze.mark_sent(spine, o1, "ana")
    obveze.mark_sent(spine, o2, "ana")
    obveze.mark_sent(spine, o3, "boris")
    act = obveze.worker_activity(spine)
    by = {r["worker"]: r for r in act}
    assert by["ana"]["closed"] == 2 and by["boris"]["closed"] == 1
    assert by["ana"]["last_at"] and by["boris"]["last_at"]
    # neposlane se ne broje
    assert sum(r["closed"] for r in act) == 3


def test_worker_activity_since_filter(spine):
    o1, o2, _ = _seed_obligations(spine)
    with spine.write() as c:  # stara zatvorena obveza
        c.execute("INSERT INTO obligation_status(obligation_id,sent,sent_by,sent_at) "
                  "VALUES(?,1,'ana','2020-01-01 10:00:00')", (o1,))
    obveze.mark_sent(spine, o2, "ana")  # danas
    recent = obveze.worker_activity(spine, since="2026-01-01")
    assert {r["worker"]: r["closed"] for r in recent} == {"ana": 1}  # stara ispala


def test_worker_closed_detail(spine):
    o1, _, o3 = _seed_obligations(spine)
    obveze.mark_sent(spine, o1, "ana")
    obveze.mark_sent(spine, o3, "ana")
    rows = obveze.worker_closed(spine, "ana")
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["JOPPD", "PDV"]
    assert all(r["client"] and r["sent_at"] for r in rows)


def _admin(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_activity_endpoint_admin_only(spine, cfg):
    c, ha = _admin(spine, cfg)
    add_user(spine, "radnik", "pw")
    tok = c.post("/auth/login", json={"username": "radnik", "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username='radnik'").fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, "member")
    assert c.get("/obveze/aktivnost", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    o1, _, _ = _seed_obligations(spine)
    obveze.mark_sent(spine, o1, "ana")
    r = c.get("/obveze/aktivnost", headers=ha)
    assert r.status_code == 200 and any(w["worker"] == "ana" for w in r.json())


def test_activity_page(spine, cfg):
    c, ha = _admin(spine, cfg)
    c.post("/auth/login", json={"username": "gazda", "password": "pw"})
    r = c.get("/ui/obveze-aktivnost", headers=ha)
    assert r.status_code == 200 and "aktivnost" in r.text.lower()
