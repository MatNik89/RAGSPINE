"""Autonomija: nenadzirani run PARKIRA write-radnje (ne dira podatke), vlasnik
odobri/odbaci. HIGH-rizik uvijek parkiran. 'Priprema -> potpis'."""
import pytest

from atlas.business import acl, parked, tenancy
from atlas.core.llm import LLMResult
from atlas.rag import agent


def _actor(spine, role="member", uid=1, username="ana"):
    from atlas.web.deps import add_user
    add_user(spine, username, "pw", role)
    return acl.Actor(user_id=uid, org_id=tenancy.default_org_id(spine), role=role, username=username)


def _client(spine, name="Pekara"):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name) VALUES(?)", (name,)).lastrowid


class FakeLLM:
    def __init__(self, script): self.script = list(script)
    def supports_tools(self): return True
    def complete(self, *a, **k): return self.script.pop(0)


def _r(text="", calls=None):
    return LLMResult(text=text, model="f", usage={}, tool_calls=calls or [])


def test_unattended_parks_write_and_does_not_touch_data(spine, cfg):
    a = _actor(spine)
    _client(spine, "Firma")
    llm = FakeLLM([_r(calls=[{"name": "zapisi_belesku", "args": {"klijent": "Firma", "tekst": "nacrt"}}]),
                  _r(text="pripremljeno")])
    out = agent.run_unattended(spine, cfg, "pripremi bilješku", a, llm, source="test")
    assert len(out["parkirano"]) == 1
    # NIŠTA nije zapisano (samo parkirano)
    assert spine.read().execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 0
    pend = parked.list_pending(spine, a.org_id)
    assert len(pend) == 1 and pend[0]["tool"] == "zapisi_belesku"


def test_owner_approves_parked_executes(spine, cfg):
    a = _actor(spine, role="owner", username="gazda")
    _client(spine, "Firma")
    pid = parked.park(spine, a.org_id, "test", "zapisi_belesku",
                      {"klijent": "Firma", "tekst": "nacrt"}, "Zapisat ću bilješku", "med")
    res = parked.approve(spine, cfg, pid, a)
    assert res["tool"] == "zapisi_belesku"
    assert spine.read().execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 1
    # dvaput odobriti ne ide
    with pytest.raises(ValueError):
        parked.approve(spine, cfg, pid, a)


def test_reject_parked(spine, cfg):
    a = _actor(spine, role="owner", username="g")
    pid = parked.park(spine, a.org_id, "t", "zapisi_belesku", {"klijent": "X"}, "s", "med")
    assert parked.reject(spine, pid, a) is True
    assert parked.list_pending(spine, a.org_id) == []


def test_high_risk_always_parked_in_unattended(spine, cfg):
    a = _actor(spine)
    _client(spine, "Firma")
    # čak i uz (nemoguć) grant, high se parkira; ovdje bez granta -> parkiran
    llm = FakeLLM([_r(calls=[{"name": "posalji_poruku_klijentu",
                              "args": {"klijent": "Firma", "naslov": "N", "tekst": "T"}}]),
                  _r(text="gotovo")])
    out = agent.run_unattended(spine, cfg, "javi klijentu", a, llm, source="t")
    assert len(out["parkirano"]) == 1
    assert spine.read().execute(
        "SELECT risk FROM parked_actions WHERE id=?", (out["parkirano"][0],)).fetchone()["risk"] == "high"


def test_unattended_auto_executes_with_grant(spine, cfg):
    from atlas.business import agent_grants
    a = _actor(spine)
    _client(spine, "Firma")
    agent_grants.create_grant(spine, a, "zapisi_belesku", {"klijent": "Firma"}, scope="user")
    llm = FakeLLM([_r(calls=[{"name": "zapisi_belesku", "args": {"klijent": "Firma", "tekst": "auto"}}]),
                  _r(text="ok")])
    out = agent.run_unattended(spine, cfg, "zapiši", a, llm, source="t")
    assert out["izvrseno"] == ["zapisi_belesku"] and out["parkirano"] == []
    assert spine.read().execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 1


def test_parkirano_endpoints_owner_only(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    ho = {"Authorization": "Bearer " + c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]}
    add_user(spine, "m", "pw")
    tm = c.post("/auth/login", json={"username": "m", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='m'").fetchone()["id"], "member")
    assert c.get("/parkirano", headers={"Authorization": f"Bearer {tm}"}).status_code == 403
    assert c.get("/parkirano", headers=ho).status_code == 200


def test_autonomni_action_registered():
    from atlas.business import scheduler_tasks as st
    assert "autonomni_pregled" in st.ACTIONS
    assert any(a["key"] == "autonomni_pregled" for a in st.action_labels())


def test_unattended_denies_izvezi_excel(spine, cfg):
    a = _actor(spine)
    llm = FakeLLM([_r(calls=[{"name": "izvezi_excel", "args": {"sto": "klijenti"}}]),
                  _r(text="gotovo")])
    out = agent.run_unattended(spine, cfg, "izvezi", a, llm, source="t")
    assert out["parkirano"] == [] and out["izvrseno"] == []  # nije izvršeno ni parkirano


def test_unattended_forces_pretrazi_no_web(spine, cfg):
    a = _actor(spine)
    seen = {}
    import atlas.rag.agent_tools as at
    orig = at.run_tool
    def spy(spine_, cfg_, actor_, name, args):
        if name == "pretrazi":
            seen["web"] = args.get("web")
            return {"lokalno": []}
        return orig(spine_, cfg_, actor_, name, args)
    at.run_tool = spy
    try:
        llm = FakeLLM([_r(calls=[{"name": "pretrazi", "args": {"upit": "x", "web": True}}]),
                      _r(text="ok")])
        agent.run_unattended(spine, cfg, "traži", a, llm, source="t")
    finally:
        at.run_tool = orig
    assert seen.get("web") is False  # web egress ugašen u autonomiji


def test_parked_list_exposes_args(spine, cfg):
    a = _actor(spine, role="owner", username="g")
    parked.park(spine, a.org_id, "t", "posalji_poruku_klijentu",
                {"klijent": "X", "naslov": "N", "tekst": "TAJNO-TIJELO"}, "Poslat ću", "high")
    pend = parked.list_pending(spine, a.org_id)
    assert pend[0]["args"]["tekst"] == "TAJNO-TIJELO"  # vlasnik vidi sadržaj prije odobrenja
