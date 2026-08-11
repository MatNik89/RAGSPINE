"""Approval-grantovi: potvrdi-jednom-zapamti; SAFETY-FLOOR: high-rizik NIKAD auto."""
import pytest

from atlas.business import acl, agent_grants, tenancy
from atlas.rag import agent, agent_tools


def _actor(spine, role="member", uid=1):
    return acl.Actor(user_id=uid, org_id=tenancy.default_org_id(spine), role=role, username="ana")


def _client(spine, name="Pekara"):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name) VALUES(?)", (name,)).lastrowid


def test_high_risk_never_auto_even_with_forced_grant(spine, cfg):
    a = _actor(spine)
    # ubaci grant IZRAVNO u bazu za high-rizik alat (zaobiđi create_grant koji bi odbio)
    with spine.write() as c:
        c.execute("INSERT INTO agent_grants(org_id,scope,user_id,tool,target,max_risk) "
                  "VALUES(?,?,?,?,?,?)", (a.org_id, "org", a.user_id,
                                          "posalji_poruku_klijentu", "", "high"))
    assert agent_grants.can_auto_approve(spine, a, "posalji_poruku_klijentu",
                                         {"klijent": "X"}) is False  # SAFETY FLOOR



def test_med_write_auto_approves_with_grant(spine, cfg):
    a = _actor(spine)
    _client(spine)
    with spine.write() as c:  # obveza da oznaci_obvezu prođe
        cid = spine.read().execute("SELECT id FROM clients WHERE name='Pekara'").fetchone()["id"]
        c.execute("INSERT INTO obligations(client_id, kind, period) VALUES(?,?,?)",
                  (cid, "PDV", "2026-08"))
    agent_grants.create_grant(spine, a, "oznaci_obvezu",
                              {"klijent": "Pekara", "vrsta": "PDV"}, scope="user", user="ana")
    assert agent_grants.can_auto_approve(spine, a, "oznaci_obvezu",
                                         {"klijent": "Pekara", "vrsta": "PDV", "stanje": True}) is True
    # drugi klijent (drugi cilj) NE prolazi
    assert agent_grants.can_auto_approve(spine, a, "oznaci_obvezu",
                                         {"klijent": "Drugi", "vrsta": "PDV"}) is False


def test_grant_scope_user_isolated(spine, cfg):
    a = _actor(spine, uid=1)
    b = _actor(spine, uid=2)
    agent_grants.create_grant(spine, a, "zapisi_belesku", {"klijent": "Pekara"}, scope="user")
    assert agent_grants.can_auto_approve(spine, a, "zapisi_belesku", {"klijent": "Pekara", "tekst": "x"}) is True
    assert agent_grants.can_auto_approve(spine, b, "zapisi_belesku", {"klijent": "Pekara", "tekst": "x"}) is False


def test_revoke_stops_auto(spine, cfg):
    a = _actor(spine)
    gid = agent_grants.create_grant(spine, a, "zapisi_belesku", {"klijent": "Pekara"}, scope="user")
    assert agent_grants.can_auto_approve(spine, a, "zapisi_belesku", {"klijent": "Pekara", "tekst": "x"}) is True
    agent_grants.revoke_grant(spine, a, gid, is_owner=False, user="ana")
    assert agent_grants.can_auto_approve(spine, a, "zapisi_belesku", {"klijent": "Pekara", "tekst": "x"}) is False


def test_create_grant_high_rejected(spine, cfg):
    a = _actor(spine)
    with pytest.raises(ValueError):
        agent_grants.create_grant(spine, a, "posalji_poruku_klijentu", {"klijent": "X"})


def test_run_agent_auto_executes_with_grant(spine, cfg):
    from atlas.core.llm import LLMResult
    a = _actor(spine)
    _client(spine, "Nova")
    agent_grants.create_grant(spine, a, "zapisi_belesku", {"klijent": "Nova"}, scope="user")

    class FakeLLM:
        def __init__(self, r): self.r = r
        def supports_tools(self): return True
        def complete(self, *x, **k): return self.r
    r = LLMResult(text="", model="f", usage={},
                  tool_calls=[{"name": "zapisi_belesku", "args": {"klijent": "Nova", "tekst": "auto"}}])
    out = agent.run_agent(spine, cfg, "zapiši bilješku", a, FakeLLM(r))
    assert out["pending"] is None and "automatski odobreno" in out["text"]
    # bilješka STVARNO zapisana (auto-izvršeno)
    n = spine.read().execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"]
    assert n == 1


def test_confirm_with_remember_creates_grant(spine, cfg):
    a = _actor(spine)
    _client(spine, "Firma")
    token = agent.stash_pending(spine, a, {"tool": "zapisi_belesku",
                                           "args": {"klijent": "Firma", "tekst": "prva"}})
    out = agent.confirm_pending(spine, cfg, token, a, remember=True)
    assert out["remembered"] is True
    # sad je zapamćeno -> idući isti poziv se auto-odobrava
    assert agent_grants.can_auto_approve(spine, a, "zapisi_belesku",
                                         {"klijent": "Firma", "tekst": "druga"}) is True


def test_confirm_remember_high_risk_not_remembered(spine, cfg):
    a = _actor(spine)
    _client(spine, "Firma")
    # posalji_poruku je high -> confirm izvrši ali NE zapamti (safety-floor)
    with spine.write() as c:
        cid = spine.read().execute("SELECT id FROM clients WHERE name='Firma'").fetchone()["id"]
        c.execute("UPDATE clients SET messaging_consent=0 WHERE id=?", (cid,))
    token = agent.stash_pending(spine, a, {"tool": "posalji_poruku_klijentu",
                                           "args": {"klijent": "Firma", "naslov": "N", "tekst": "T"}})
    out = agent.confirm_pending(spine, cfg, token, a, remember=True)
    assert out["remembered"] is False  # high se ne pamti


def test_grants_endpoints(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    ho = {"Authorization": "Bearer " + c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]}
    _client(spine, "Klijent")
    # user-scope grant
    r = c.post("/grants", headers=ho, json={"tool": "zapisi_belesku", "args": {"klijent": "Klijent"}})
    assert r.status_code == 200
    gid = r.json()["id"]
    assert any(g["id"] == gid for g in c.get("/grants", headers=ho).json()["grants"])
    # high-rizik odbijen
    assert c.post("/grants", headers=ho, json={"tool": "posalji_poruku_klijentu", "args": {"klijent": "X"}}).status_code == 400
    # revoke
    assert c.request("DELETE", f"/grants/{gid}", headers=ho).json()["ok"] is True


def test_org_grant_requires_owner(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    add_user(spine, "m", "pw")
    tm = c.post("/auth/login", json={"username": "m", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='m'").fetchone()["id"], "member")
    _client(spine, "K")
    assert c.post("/grants", headers={"Authorization": f"Bearer {tm}"},
                  json={"tool": "zapisi_belesku", "args": {"klijent": "K"}, "scope": "org"}).status_code == 403


def test_empty_target_rejected(spine, cfg):
    a = _actor(spine)
    with pytest.raises(ValueError):  # dodaj_klijenta bez naziva = wildcard -> odbij
        agent_grants.create_grant(spine, a, "dodaj_klijenta", {})


def test_viewer_cannot_create_grant(spine, cfg):
    v = _actor(spine, role="viewer", uid=9)
    with pytest.raises(ValueError):
        agent_grants.create_grant(spine, v, "zapisi_belesku", {"klijent": "X"})


def test_org_grant_target_masked_for_non_admin(spine, cfg):
    owner = _actor(spine, role="owner", uid=1)
    agent_grants.create_grant(spine, owner, "zapisi_belesku", {"klijent": "TajniKlijent"}, scope="org")
    member = _actor(spine, role="member", uid=2)
    grants = agent_grants.list_grants(spine, member)
    org = [g for g in grants if g["scope"] == "org"][0]
    assert "TajniKlijent" not in org["target"] and org["target"] == "…"
    # admin vidi pravi cilj
    admin = _actor(spine, role="admin", uid=3)
    assert "TajniKlijent" in [g for g in agent_grants.list_grants(spine, admin) if g["scope"] == "org"][0]["target"]


def test_target_no_pipe_collision(spine, cfg):
    a = _actor(spine)
    # vrijednosti s '|' ne smiju kolidirati (kanonski JSON)
    t1 = agent_grants.target_for("zapisi_belesku", {"klijent": "A|B"})
    t2 = agent_grants.target_for("zapisi_belesku", {"klijent": "A", "x": "B"})
    assert t1 != t2
