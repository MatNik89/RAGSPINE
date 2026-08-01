from fastapi.testclient import TestClient

from ragspine.business import obveze
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _seed(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Zebra', '1', 'u sustavu pdv')")
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Alfa', '2', 'u sustavu pdv')")
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Beta', '3', 'nije u pdvu')")


def test_only_pdv_clients(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    assert [r["client"] for r in rows] == ["Alfa", "Zebra"]  # Beta van, abecedno


def test_mark_sent_moves_down(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    obveze.mark_sent(spine, rows[0]["obligation_id"], "ana")
    rows2 = obveze.list_period(spine, "PDV", "2026-07")
    assert rows2[-1]["client"] == "Alfa" and rows2[-1]["sent"]


def test_ensure_period_idempotent(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    assert len(rows) == 2


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_api_obveze_html_escapes_client_name(spine, cfg):
    with spine.write() as conn:
        conn.execute(
            "INSERT INTO clients(name, oib, pdv_status) VALUES ('<script>Zloća</script>', '9', 'u sustavu pdv')"
        )
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-07", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<script>Zloća</script>" not in r.text
    assert "&lt;script&gt;Zloća&lt;/script&gt;" in r.text
