"""Parkirane radnje: nenadzirani (autonomni) agent-run pripremi posao ali NE dira
podatke — svaku write-radnju stavi u red za ODOBRENJE (OpenWorker inbox-park +
MateClaw in_review). Vlasnik kasnije pregleda i odobri/odbije. Autonomija je u
PRIPREMI (read/draft); gate ostaje na svakoj mutaciji. HIGH-rizik uvijek ovamo."""
import json


def park(spine, org_id, source: str, tool: str, args: dict, summary: str, risk: str) -> int:
    with spine.write() as c:
        return c.execute(
            "INSERT INTO parked_actions(org_id,source,tool,args_json,summary,risk,created_by) "
            "VALUES(?,?,?,?,?,?,?)",
            (org_id, source, tool, json.dumps(args, ensure_ascii=False, default=str),
             summary, risk, source)).lastrowid


def list_pending(spine, org_id, limit: int = 100) -> list[dict]:
    rows = spine.read().execute(
        "SELECT id, source, tool, summary, risk, created_at FROM parked_actions "
        "WHERE org_id=? AND status='pending' ORDER BY id DESC LIMIT ?",
        (org_id, limit)).fetchall()
    return [dict(r) for r in rows]


def approve(spine, cfg, park_id: int, actor) -> dict:
    """Atomično preuzmi pending -> IZVRŠI kroz run_tool (svježa provjera ovlasti) ->
    označi approved. Ovlast se provjerava kao za svaku radnju (approver = actor)."""
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
    except ValueError:
        with spine.write() as c:  # neuspjeh -> vrati na pending da se može opet
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
