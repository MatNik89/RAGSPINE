"""Self-evolving skills (Abu obrazac): nakon ponovljivog toka agent može
PREDLOŽITI da se procedura spremi kao vještina. Reciklira postojeću mašineriju —
predlozi_vjestinu je WRITE alat pa ide kroz isti propose->confirm token; potvrda
kreira DRAFT vještinu (owner=predlagatelj, org-scoped) koju ured kasnije aktivira.
Draft ne curi u prompt-katalog (list_skills filtrira status='active')."""
import json

from atlas.business import acl, tenancy
from atlas.core.llm import LLMResult
from atlas.knowledge import skills as skills_mod
from atlas.rag import agent, agent_tools
from atlas.web import deps


def _actor(spine, username="ana", role="member", user_id=1):
    deps.add_user(spine, username, "pw")
    return acl.Actor(user_id=user_id, org_id=tenancy.default_org_id(spine),
                     role=role, username=username)


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)

    def supports_tools(self):
        return True

    def complete(self, messages, system=None, model=None, max_tokens=1024,
                 temperature=0.2, tools=None):
        return self.script.pop(0)


def _result(tool_calls=None):
    return LLMResult(text="", model="fake", usage={}, tool_calls=tool_calls or [])


def test_propose_skill_tool_registered_write_and_med_risk():
    t = agent_tools.TOOLS.get("predlozi_vjestinu")
    assert t is not None and not t.readonly and t.min_role == "member"
    assert agent_tools.risk("predlozi_vjestinu") == "med"


def test_propose_skill_requires_name_and_steps():
    ok, _ = agent_tools.validate("predlozi_vjestinu", {"ime": "X"})
    assert not ok  # koraci obavezni
    ok, _ = agent_tools.validate("predlozi_vjestinu", {"koraci": "1. ..."})
    assert not ok  # ime obavezno
    ok, _ = agent_tools.validate("predlozi_vjestinu", {"ime": "X", "koraci": "1. ..."})
    assert ok


def test_agent_proposes_skill_as_pending(spine, cfg):
    a = _actor(spine)
    llm = FakeLLM([_result(tool_calls=[{"name": "predlozi_vjestinu", "args": {
        "ime": "Mjesečni PDV", "koraci": "1. povuci obveze\n2. označi poslano"}}])])
    out = agent.run_agent(spine, cfg, "spremi ovo kao vještinu", a, llm)
    assert out["pending"]["tool"] == "predlozi_vjestinu"
    assert out["pending"]["risk"] == "med"
    # ništa nije stvarno spremljeno dok se ne potvrdi
    assert skills_mod.list_skills(spine, a.org_id) == []


def test_confirm_creates_draft_skill_owned_by_proposer(spine, cfg):
    a = _actor(spine)
    pending = {"tool": "predlozi_vjestinu",
               "args": {"ime": "Mjesečni PDV", "opis": "rutina",
                        "koraci": "1. povuci\n2. označi"}}
    token = agent.stash_pending(spine, a, pending)
    agent.confirm_pending(spine, cfg, token, a)
    rows = skills_mod.list_skills(spine, a.org_id)
    assert len(rows) == 1
    s = rows[0]
    assert s["name"] == "Mjesečni PDV" and s["status"] == "draft"
    assert s["owner_user_id"] == a.user_id and s["org_id"] == a.org_id
    assert "povuci" in s["steps"]


def test_draft_skill_not_in_active_catalog(spine, cfg):
    a = _actor(spine)
    token = agent.stash_pending(spine, a, {"tool": "predlozi_vjestinu",
        "args": {"ime": "Nacrt", "koraci": "1. x"}})
    agent.confirm_pending(spine, cfg, token, a)
    assert skills_mod.list_skills(spine, a.org_id, status="active") == []


def test_skills_create_endpoint_denies_viewer(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    add_user(spine, "v", "pw")
    tv = c.post("/auth/login", json={"username": "v", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='v'").fetchone()["id"], "viewer")
    r = c.post("/skills", headers={"Authorization": f"Bearer {tv}"},
               json={"name": "X", "steps": "1", "visibility": "org"})
    assert r.status_code == 403  # viewer ne smije autorirati vještinu


def test_activate_shared_skill_requires_admin(spine, cfg):
    # member kreira org-vidljivu vještinu, ali je NE smije sam aktivirati (u prompt svih)
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    add_user(spine, "m", "pw")
    tm = c.post("/auth/login", json={"username": "m", "password": "pw"}).json()["token"]
    hm = {"Authorization": f"Bearer {tm}"}
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='m'").fetchone()["id"], "member")
    sid = c.post("/skills", headers=hm, json={"name": "Org vj", "steps": "1", "visibility": "org"}).json()["id"]
    r = c.post(f"/skills/{sid}/status", headers=hm, json={"status": "active"})
    assert r.status_code == 403


def test_create_skill_caps_fields_and_count(spine, cfg):
    from atlas.business import tenancy as tn
    org = tn.default_org_id(spine)
    sid = skills_mod.create_skill(spine, org, "N" * 500, description="D" * 900,
                                  steps="S" * 20000, owner_user_id=1)
    s = skills_mod.get_skill(spine, sid)
    assert len(s["name"]) <= 120 and len(s["description"]) <= 300 and len(s["steps"]) <= 8000


def test_confirm_summary_shows_steps(spine, cfg):
    a = _actor(spine)
    out = agent.summarize_action("predlozi_vjestinu",
                                 {"ime": "PDV", "opis": "rutina", "koraci": "1. povuci\n2. označi"})
    assert "povuci" in out and "rutina" in out  # sadržaj vidljiv u potvrdi
