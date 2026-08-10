"""Progresivno otkrivanje vještina AGENTU (nad postojećim skills-registrom):
katalog (ime+opis) u promptu, pune korake učita alat ucitaj_vjestinu na zahtjev."""
from atlas.business import acl, tenancy
from atlas.knowledge import skills as skills_mod
from atlas.rag import agent, agent_tools


def _actor(spine, role="member", uid=1):
    return acl.Actor(user_id=uid, org_id=tenancy.default_org_id(spine), role=role, username="ana")


def _mk_skill(spine, org, name, desc, steps, status="active"):
    sid = skills_mod.create_skill(spine, org, name, desc, trigger="", steps=steps,
                                  owner_user_id=1, visibility="org")
    if status != "draft":
        skills_mod.set_status(spine, sid, status)
    return sid


def test_catalog_lists_active_only_names_and_desc(spine, cfg):
    org = tenancy.default_org_id(spine)
    _mk_skill(spine, org, "PDV", "Kako predati PDV", "TAJNI-KORACI")
    _mk_skill(spine, org, "Draft", "nedovršeno", "x", status="draft")
    txt = agent._skills_catalog_text(spine, _actor(spine))
    assert "PDV: Kako predati PDV" in txt
    assert "TAJNI-KORACI" not in txt          # koraci se NE injektiraju unaprijed
    assert "Draft" not in txt                 # samo aktivne


def test_tool_loads_steps_on_demand(spine, cfg):
    org = tenancy.default_org_id(spine)
    _mk_skill(spine, org, "Zatvaranje", "Mjesečno", "KORAK-A pa KORAK-B")
    out = agent_tools.run_tool(spine, cfg, _actor(spine), "ucitaj_vjestinu", {"ime": "zatvaranje"})
    assert out["ime"] == "Zatvaranje" and "KORAK-A" in out["koraci"]


def test_tool_unknown_lists_available(spine, cfg):
    org = tenancy.default_org_id(spine)
    _mk_skill(spine, org, "PDV", "d", "s")
    out = agent_tools.run_tool(spine, cfg, _actor(spine), "ucitaj_vjestinu", {"ime": "nema"})
    assert "greska" in out and "PDV" in out["dostupne"]


def test_no_skills_empty_catalog(spine, cfg):
    assert agent._skills_catalog_text(spine, _actor(spine)) == ""


def test_tool_registered_readonly():
    assert agent_tools.TOOLS["ucitaj_vjestinu"].readonly is True
