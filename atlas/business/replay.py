"""Replay: owner ponovno pokrene RANIJE IZVRŠENU agent-write-radnju iz audit-traga
(npr. 'ponovi jučerašnju kampanju'). Izvršava se kroz run_tool -> SVE provjere
ovlasti/consent/vidljivosti vrijede PONOVNO (isti gate kao svaka radnja). Owner-gated,
audit-irano kao 'replay'. Ne troši budžet (ljudska radnja, ne autonomni runaway).
Reuse audit_log — bez nove tablice."""
import json

# samo zapisi gdje je entity=čisto ime alata, detail=args_json (agent confirm / auto-grant).
# parked_approve NAMJERNO izostavljen: njegov entity je 'tool:park_id' (ne čist alat).
_REPLAYABLE = ("agent_execute", "agent_auto_grant")


def list_replayable(spine, limit: int = 50) -> list[dict]:
    rows = spine.read().execute(
        "SELECT id, user, action, entity AS tool, detail AS args_json, at "
        "FROM audit_log WHERE action IN (?,?) ORDER BY id DESC LIMIT ?",
        (*_REPLAYABLE, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["args"] = json.loads(d.pop("args_json") or "{}")  # owner VIDI što ponavlja
        except (ValueError, TypeError):
            d["args"] = {}
        out.append(d)
    return out


def replay(spine, cfg, audit_id: int, actor) -> dict:
    """Ponovi jednu radnju iz audit-traga kroz run_tool (svjež auth/consent-check)."""
    from atlas.rag import agent_tools
    row = spine.read().execute(
        "SELECT action, entity AS tool, detail AS args_json FROM audit_log WHERE id=?",
        (audit_id,)).fetchone()
    if row is None or row["action"] not in _REPLAYABLE:
        raise ValueError("audit-zapis ne postoji ili nije ponovljiv")
    tool = row["tool"]
    if tool not in agent_tools.TOOLS:
        raise ValueError(f"nepoznat alat: {tool!r}")
    try:
        args = json.loads(row["args_json"] or "{}")
    except (ValueError, TypeError):
        raise ValueError("neispravni argumenti u audit-zapisu")
    result = agent_tools.run_tool(spine, cfg, actor, tool, args)  # isti gate kao svaka radnja
    spine.audit(actor.username, "replay", tool, row["args_json"] or "{}")
    return {"tool": tool, "result": result}
