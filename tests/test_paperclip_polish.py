"""Branch C (Paperclip polish): memorija recency-decay u recall-rangu; replay
idempotency-debounce (dvoklik ne dvostruko izvršenje)."""
import pytest

from atlas.business import acl, replay, tenancy
from atlas.knowledge import memory_layers as ml
from atlas.rag import agent_tools
from atlas.web.deps import add_user


# ---- #10 memory recency-decay ----
def _atom(spine, content, days_old=0, org=1, uid=1):
    with spine.write() as c:
        return c.execute(
            "INSERT INTO mem_l1(org_id,user_id,kind,content,at) "
            "VALUES(?,?,?,?,datetime('now',?))",
            (org, uid, "fact", content, f"-{days_old} days")).lastrowid


def test_recall_recency_breaks_ties(spine):
    _atom(spine, "Pekara staro", days_old=60)     # cold
    _atom(spine, "Pekara novo", days_old=0)       # hot
    out = ml.recall(spine, 1, 1, "Pekara", max_items=1)
    assert out["atoms"] == ["Pekara novo"]        # svježe izbije na izjednačenom preklapanju


def test_recall_overlap_still_dominates_recency(spine):
    _atom(spine, "Pekara PDV prijava rok", days_old=90)   # veći overlap, star
    _atom(spine, "Pekara nešto", days_old=0)              # manji overlap, svjež
    out = ml.recall(spine, 1, 1, "Pekara PDV prijava", max_items=1)
    assert out["atoms"] == ["Pekara PDV prijava rok"]     # preklapanje > recency


# ---- #4 replay idempotency-debounce ----
def _owner(spine):
    add_user(spine, "ana", "pw", "owner")
    return acl.Actor(user_id=1, org_id=tenancy.default_org_id(spine), role="owner", username="ana")


def test_replay_debounce_blocks_double(spine, monkeypatch):
    replay._recent.clear()
    actor = _owner(spine)
    calls = []
    monkeypatch.setattr(agent_tools, "run_tool",
                        lambda s, c, a, t, ar: calls.append(t) or {"ok": 1})
    monkeypatch.setattr(agent_tools, "TOOLS", {"dodaj_rok": object()})
    spine.audit("ana", "agent_execute", "dodaj_rok", "{}")
    aid = spine.read().execute("SELECT max(id) AS m FROM audit_log").fetchone()["m"]
    replay.replay(spine, None, aid, actor)                # prvi prolazi
    with pytest.raises(ValueError):                       # odmah drugi -> odbijen
        replay.replay(spine, None, aid, actor)
    assert calls == ["dodaj_rok"]                         # izvršeno TOČNO jednom


def test_replay_debounce_scoped_per_action(spine, monkeypatch):
    replay._recent.clear()
    actor = _owner(spine)
    monkeypatch.setattr(agent_tools, "run_tool", lambda s, c, a, t, ar: {"ok": 1})
    monkeypatch.setattr(agent_tools, "TOOLS", {"dodaj_rok": object()})
    spine.audit("ana", "agent_execute", "dodaj_rok", '{"a":1}')
    spine.audit("ana", "agent_execute", "dodaj_rok", '{"a":2}')
    ids = [r["id"] for r in spine.read().execute(
        "SELECT id FROM audit_log WHERE action='agent_execute' ORDER BY id").fetchall()]
    replay.replay(spine, None, ids[0], actor)
    replay.replay(spine, None, ids[1], actor)             # različit audit_id -> nije debounced
