"""Replay: owner ponovi ranije izvršenu agent-write-radnju iz audit-traga kroz
run_tool (svjež auth-check). Samo agent_execute/agent_auto_grant (entity=čist alat)."""
import pytest

from atlas.business import acl, replay, tenancy


def _actor(spine, role="owner", uid=1, username="ana"):
    from atlas.web.deps import add_user
    add_user(spine, username, "pw", role)
    return acl.Actor(user_id=uid, org_id=tenancy.default_org_id(spine),
                     role=role, username=username)


def _client(spine, name="Pekara"):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name) VALUES(?)", (name,)).lastrowid


def test_list_replayable_filters_and_parses(spine):
    spine.audit("ana", "agent_execute", "oznaci_obvezu", '{"klijent":"Pekara"}')
    spine.audit("ana", "agent_auto_grant", "dodaj_rok", '{"x":1}')
    spine.audit("ana", "parked_approve", "posalji_poruku:5", '{"y":2}')  # NE ponovljiv
    spine.audit("ana", "login", "ana", "")                              # NE ponovljiv
    rows = replay.list_replayable(spine)
    tools = [r["tool"] for r in rows]
    assert tools == ["dodaj_rok", "oznaci_obvezu"]      # DESC, samo 2 write-akcije
    assert rows[1]["args"] == {"klijent": "Pekara"}     # parsirani args (owner vidi)


def test_replay_reruns_through_run_tool(spine, monkeypatch):
    actor = _actor(spine)
    calls = []
    from atlas.rag import agent_tools
    monkeypatch.setattr(agent_tools, "run_tool",
                        lambda s, c, a, tool, args: calls.append((tool, args)) or {"ok": True})
    monkeypatch.setattr(agent_tools, "TOOLS", {"dodaj_rok": object()})
    spine.audit("ana", "agent_execute", "dodaj_rok", '{"klijent":"Pekara","vrsta":"PDV"}')
    aid = spine.read().execute("SELECT max(id) AS m FROM audit_log").fetchone()["m"]
    out = replay.replay(spine, None, aid, actor)
    assert out["tool"] == "dodaj_rok"
    assert calls == [("dodaj_rok", {"klijent": "Pekara", "vrsta": "PDV"})]  # svjež poziv
    # replay je i sam audit-iran
    assert spine.read().execute(
        "SELECT count(*) AS n FROM audit_log WHERE action='replay'").fetchone()["n"] == 1


def test_replay_rejects_non_replayable_id(spine):
    actor = _actor(spine)
    spine.audit("ana", "login", "ana", "")
    aid = spine.read().execute("SELECT max(id) AS m FROM audit_log").fetchone()["m"]
    with pytest.raises(ValueError):
        replay.replay(spine, None, aid, actor)


def test_replay_rejects_unknown_tool(spine, monkeypatch):
    actor = _actor(spine)
    from atlas.rag import agent_tools
    monkeypatch.setattr(agent_tools, "TOOLS", {})  # alat više ne postoji
    spine.audit("ana", "agent_execute", "nestali_alat", "{}")
    aid = spine.read().execute("SELECT max(id) AS m FROM audit_log").fetchone()["m"]
    with pytest.raises(ValueError):
        replay.replay(spine, None, aid, actor)
