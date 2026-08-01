from fastapi.testclient import TestClient
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_doc_templates_list(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/doc/templates", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "ponuda" in r.json()


def test_doc_generate_requires_auth(spine, cfg):
    c = _client(spine, cfg)
    assert c.post("/doc/generate", json={"doc_type": "ponuda", "client_id": 1}).status_code == 401


def test_doc_generate_returns_text_and_gate(spine, cfg):
    with spine.write() as conn:
        conn.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                      ("Firma X", "12345678901", "x@firma.hr", "091", "Ana"))
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/doc/generate",
               json={"doc_type": "ponuda", "client_id": 1,
                     "extra": {"stavke": [{"naziv": "Usluga A", "iznos": 100}]}},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert "Firma X" in body["text"]
    assert body["gate"]["ok"] is True
