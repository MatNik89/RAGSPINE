"""Faza 3 T4: agentski /chat s DB pending-potvrdom.

Member+ i tool-capable LLM -> run_agent; write prijedlog se sprema kao
jednokratni token (agent_pending) i izvrši tek na /chat/potvrdi. Viewer ide
starim retrieval putem (bez pendinga). Token je vlasnički, jednokratan, istječe.
"""
import pytest
from fastapi.testclient import TestClient

from atlas.business import tenancy
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup

VALID_OIB = "10000000000"


def _login(spine, cfg, username="ana", role="member"):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, username, "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": username, "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    org_id = tenancy.default_org_id(spine)
    tenancy.add_member(spine, org_id, uid, role)  # upsert role svježa se čita
    return c, {"Authorization": f"Bearer {tok}"}, uid, org_id


def _pending_agent(**pending):
    def fake(spine, cfg, query, actor, llm, max_steps=4):
        return {"text": pending.get("summary", "prijedlog"), "sources": [], "pending": pending or None}
    return fake


def _text_agent(text="Evo odgovora.", called=None):
    def fake(spine, cfg, query, actor, llm, max_steps=4):
        if called is not None:
            called.append(query)
        return {"text": text, "sources": [], "pending": None}
    return fake


def test_agent_pending_table_exists(spine):
    with spine.write() as c:
        c.execute("INSERT INTO agent_pending(token,user_id,org_id,tool,args_json,created_at) "
                  "VALUES('t',1,1,'x','{}',datetime('now'))")
    assert spine.read().execute("SELECT COUNT(*) n FROM agent_pending").fetchone()["n"] == 1


def test_chat_member_write_returns_pending_and_stores_row(spine, cfg, monkeypatch):
    c, h, uid, org_id = _login(spine, cfg, role="member")
    monkeypatch.setattr("atlas.rag.agent.run_agent", _pending_agent(
        tool="dodaj_klijenta", args={"naziv": "Nova Firma", "oib": VALID_OIB},
        summary="Dodat ću klijenta Nova Firma."))
    r = c.post("/chat", headers=h, json={"q": "dodaj klijenta Nova Firma"})
    assert r.status_code == 200
    data = r.json()
    assert data["pending"]["summary"] == "Dodat ću klijenta Nova Firma."
    token = data["pending"]["token"]
    assert token
    row = spine.read().execute(
        "SELECT tool,user_id,org_id FROM agent_pending WHERE token=?", (token,)).fetchone()
    assert row["tool"] == "dodaj_klijenta" and row["user_id"] == uid and row["org_id"] == org_id
    # ništa nije upisano dok se ne potvrdi
    assert spine.read().execute("SELECT 1 FROM clients WHERE name=?", ("Nova Firma",)).fetchone() is None


def test_chat_potvrdi_executes_audits_and_consumes_token(spine, cfg, monkeypatch):
    c, h, uid, org_id = _login(spine, cfg, role="member")
    monkeypatch.setattr("atlas.rag.agent.run_agent", _pending_agent(
        tool="dodaj_klijenta", args={"naziv": "Pekara", "oib": VALID_OIB}, summary="..."))
    token = c.post("/chat", headers=h, json={"q": "x"}).json()["pending"]["token"]

    r = c.post("/chat/potvrdi", headers=h, json={"token": token})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert spine.read().execute("SELECT 1 FROM clients WHERE name=?", ("Pekara",)).fetchone() is not None
    assert spine.read().execute(
        "SELECT 1 FROM audit_log WHERE action='agent_execute'").fetchone() is not None
    # jednokratan: token potrošen
    assert spine.read().execute("SELECT 1 FROM agent_pending WHERE token=?", (token,)).fetchone() is None
    assert c.post("/chat/potvrdi", headers=h, json={"token": token}).status_code == 404


def test_potvrdi_foreign_token_is_404(spine, cfg, monkeypatch):
    c, h, uid, org_id = _login(spine, cfg, username="ana", role="member")
    monkeypatch.setattr("atlas.rag.agent.run_agent", _pending_agent(
        tool="dodaj_klijenta", args={"naziv": "X", "oib": VALID_OIB}, summary="..."))
    token = c.post("/chat", headers=h, json={"q": "x"}).json()["pending"]["token"]
    # drugi korisnik ne smije potvrditi tuđi token
    c2 = TestClient(create_app(spine, cfg))
    add_user(spine, "boris", "pw")
    tok2 = c2.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, org_id, spine.read().execute(
        "SELECT id FROM users WHERE username='boris'").fetchone()["id"], "member")
    r = c2.post("/chat/potvrdi", headers={"Authorization": f"Bearer {tok2}"}, json={"token": token})
    assert r.status_code == 404
    assert spine.read().execute("SELECT 1 FROM agent_pending WHERE token=?", (token,)).fetchone()


def test_potvrdi_expired_token_is_404(spine, cfg):
    c, h, uid, org_id = _login(spine, cfg, role="member")
    with spine.write() as conn:
        conn.execute("INSERT INTO agent_pending(token,user_id,org_id,tool,args_json,created_at) "
                     "VALUES('old',?,?,'dodaj_klijenta','{}',datetime('now','-11 minutes'))",
                     (uid, org_id))
    assert c.post("/chat/potvrdi", headers=h, json={"token": "old"}).status_code == 404


def test_odustani_deletes_token(spine, cfg, monkeypatch):
    c, h, uid, org_id = _login(spine, cfg, role="member")
    monkeypatch.setattr("atlas.rag.agent.run_agent", _pending_agent(
        tool="dodaj_klijenta", args={"naziv": "X", "oib": VALID_OIB}, summary="..."))
    token = c.post("/chat", headers=h, json={"q": "x"}).json()["pending"]["token"]
    assert c.post("/chat/odustani", headers=h, json={"token": token}).json()["ok"] is True
    assert spine.read().execute("SELECT 1 FROM agent_pending WHERE token=?", (token,)).fetchone() is None


def test_viewer_uses_retrieval_path_no_agent(spine, cfg, monkeypatch):
    c, h, uid, org_id = _login(spine, cfg, role="viewer")
    called = []
    monkeypatch.setattr("atlas.rag.agent.run_agent", _text_agent(called=called))
    r = c.post("/chat", headers=h, json={"q": "kolika je stopa PDV-a"})
    assert r.status_code == 200
    assert "pending" not in r.json()  # viewer nema agentski put
    assert called == []  # run_agent nije zvan


def test_viewer_cannot_learn_via_chat_fallback(spine, cfg):
    # MEDIUM fold: viewer ide u _answer -> learn lane, koji je sad member-gated;
    # nema mrežnog dohvata ni upisa (odbijanje je prije fetcha).
    c, h, uid, org_id = _login(spine, cfg, role="viewer")
    r = c.post("/chat", headers=h, json={"q": "nauči s https://attacker.example/x"})
    assert r.status_code == 200
    assert spine.get_override("kalkulator", "prirez.Sisak") is None
    assert spine.read().execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 0


def test_potvrdi_reevaluates_authority_at_confirm_time(spine, cfg, monkeypatch):
    # token napravljen kao member; do potvrde uloga spuštena na viewer ->
    # run_tool odbija write (svježa provjera ovlasti), token svejedno potrošen
    c, h, uid, org_id = _login(spine, cfg, role="member")
    monkeypatch.setattr("atlas.rag.agent.run_agent", _pending_agent(
        tool="dodaj_klijenta", args={"naziv": "Nedopusteno", "oib": VALID_OIB}, summary="..."))
    token = c.post("/chat", headers=h, json={"q": "x"}).json()["pending"]["token"]
    tenancy.add_member(spine, org_id, uid, "viewer")  # degradacija
    r = c.post("/chat/potvrdi", headers=h, json={"token": token})
    assert r.status_code == 400
    assert spine.read().execute("SELECT 1 FROM clients WHERE name='Nedopusteno'").fetchone() is None
