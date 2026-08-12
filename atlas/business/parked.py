"""Parked actions: an unattended (autonomous) agent run prepares work but does NOT
touch data — it queues every write action for APPROVAL (OpenWorker inbox-park +
MateClaw in_review). The owner reviews and approves/rejects later. Autonomy is in the
PREPARATION (read/draft); the gate stays on every mutation. HIGH-risk always goes here."""
import json


def park(spine, org_id, source: str, tool: str, args: dict, summary: str, risk: str) -> int:
    with spine.write() as c:
        return c.execute(
            "INSERT INTO parked_actions(org_id,source,tool,args_json,summary,risk,created_by) "
            "VALUES(?,?,?,?,?,?,?)",
            (org_id, source, tool, json.dumps(args, ensure_ascii=False, default=str),
             summary, risk, source)).lastrowid


def list_pending(spine, org_id, limit: int = 100) -> list[dict]:
    # also returns the (parsed) ARGUMENTS so the owner SEES what will run before
    # approving — e.g. the client message body the AI composed (Codex: otherwise a
    # blind approval of model-controlled content)
    rows = spine.read().execute(
        "SELECT id, source, tool, summary, risk, args_json, created_at FROM parked_actions "
        "WHERE org_id=? AND status='pending' ORDER BY id DESC LIMIT ?",
        (org_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["args"] = json.loads(d.pop("args_json") or "{}")
        except (ValueError, TypeError):
            d["args"] = {}
        out.append(d)
    return out


def approve(spine, cfg, park_id: int, actor) -> dict:
    """Atomically claim the pending row -> EXECUTE through run_tool (fresh authorization
    check) -> mark approved. Authorization is checked as for any action (approver = actor).
    ValueError messages surface to the UI -> Croatian."""
    from atlas.rag import agent_tools
    with spine.write() as c:
        row = c.execute("SELECT tool, args_json FROM parked_actions "
                        "WHERE id=? AND org_id=? AND status='pending'",
                        (park_id, actor.org_id)).fetchone()
        if row is None:
            raise ValueError("parkirana radnja ne postoji ili je već obrađena")
        claim = c.execute("UPDATE parked_actions SET status='approving' "
                          "WHERE id=? AND status='pending'", (park_id,))
        if claim.rowcount != 1:
            raise ValueError("radnja je već obrađena")
        tool, args_json = row["tool"], row["args_json"]
    try:
        result = agent_tools.run_tool(spine, cfg, actor, tool, json.loads(args_json))
    except Exception:  # ANY failure -> back to pending (don't leave 'approving'; Codex)
        with spine.write() as c:
            c.execute("UPDATE parked_actions SET status='pending' WHERE id=?", (park_id,))
        raise
    with spine.write() as c:
        c.execute("UPDATE parked_actions SET status='approved', resolved_by=?, "
                  "resolved_at=datetime('now') WHERE id=?", (actor.username, park_id))
    spine.audit(actor.username, "parked_approve", f"{tool}:{park_id}", args_json)
    return {"tool": tool, "result": result}


def reject(spine, park_id: int, actor) -> bool:
    with spine.write() as c:
        cur = c.execute("UPDATE parked_actions SET status='rejected', resolved_by=?, "
                        "resolved_at=datetime('now') WHERE id=? AND org_id=? AND status='pending'",
                        (actor.username, park_id, actor.org_id))
    if cur.rowcount:
        spine.audit(actor.username, "parked_reject", f"park:{park_id}")
    return bool(cur.rowcount)
