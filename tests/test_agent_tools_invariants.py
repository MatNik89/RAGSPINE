"""Sigurnosni invarijanti registra alata (RISK #1: migrirani alat bez gate-a =
tiha eskalacija). Ovi testovi padaju čim netko doda alat koji krši pravilo."""
import pytest

from atlas.business import tenancy
from atlas.business.acl import Actor
from atlas.rag import agent_tools


def _actor(spine, role):
    return Actor(user_id=1, org_id=tenancy.default_org_id(spine), role=role, username="x")


def test_every_write_tool_requires_at_least_member():
    # WRITE alat NIKAD ne smije biti dostupan viewer-u (samo čitanje za viewer-a).
    bad = [n for n, t in agent_tools.TOOLS.items()
           if not t.readonly and t.min_role == "viewer"]
    assert bad == [], f"write alati bez gate-a (viewer smije pisati): {bad}"


def test_every_tool_has_known_role_and_schema():
    for name, t in agent_tools.TOOLS.items():
        assert t.min_role in agent_tools._ROLE_RANK, f"{name}: nepoznata min_role {t.min_role!r}"
        assert isinstance(t.schema, dict) and t.schema.get("type") == "object", \
            f"{name}: shema mora biti JSON object"
        assert callable(t.run), f"{name}: run mora biti pozivljiv"


def test_run_tool_blocks_viewer_on_every_write(spine, cfg):
    # Ne samo deklaracija — run_tool STVARNO odbija viewer-a na svakom write alatu
    # prije ikakvog izvršenja (gate je u run_tool, ne u pojedinom run-u).
    viewer = _actor(spine, "viewer")
    for name, t in agent_tools.TOOLS.items():
        if t.readonly:
            continue
        with pytest.raises(ValueError, match="ne smije"):
            agent_tools.run_tool(spine, cfg, viewer, name, {})


def test_role_rank_is_monotonic():
    assert agent_tools._ROLE_RANK == {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
