"""Replay: owner re-runs a PREVIOUSLY EXECUTED agent write-action from the audit trail
(e.g. 'repeat yesterday's campaign'). Runs through run_tool -> ALL authorization/
consent/visibility checks apply AGAIN (same gate as any action). Owner-gated, audited
as 'replay'. Does not spend budget (human action, not autonomous runaway).
Reuses audit_log — no new table."""
import json
import threading
import time

# only rows where entity=clean tool name, detail=args_json (agent confirm / auto-grant).
# parked_approve DELIBERATELY excluded: its entity is 'tool:park_id' (not a clean tool).
_REPLAYABLE = ("agent_execute", "agent_auto_grant")

# idempotency-debounce (Paperclip): a double-click/retry on POST /replay/{id} must NOT
# execute the accounting write twice. In-memory (single process), short window; an
# intentional re-run later is still possible. ponytail: in-memory TTL set; upgrade =
# client idempotency token if there is ever more than one process.
_DEBOUNCE_S = 10.0
_recent: dict = {}  # (user_id, audit_id) -> monotonic ts
_recent_lock = threading.Lock()  # check-then-set must be atomic (Codex: 2 concurrent requests)


def _debounce_claim(user_id: int, audit_id: int) -> bool:
    """True if this is the first (allowed) call; False if a duplicate within the window.
    The claim is set BEFORE execution to prevent a concurrent double-click. The whole
    check-then-set is under the lock (otherwise two parallel requests would both pass)."""
    with _recent_lock:
        now = time.monotonic()
        if len(_recent) > 256:  # occasionally purge stale entries (bounded growth)
            for k, ts in list(_recent.items()):
                if now - ts >= _DEBOUNCE_S:
                    _recent.pop(k, None)
        key = (user_id, audit_id)
        prev = _recent.get(key)
        if prev is not None and now - prev < _DEBOUNCE_S:
            return False
        _recent[key] = now
        return True


def list_replayable(spine, limit: int = 50) -> list[dict]:
    rows = spine.read().execute(
        "SELECT id, user, action, entity AS tool, detail AS args_json, at "
        "FROM audit_log WHERE action IN (?,?) ORDER BY id DESC LIMIT ?",
        (*_REPLAYABLE, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["args"] = json.loads(d.pop("args_json") or "{}")  # owner SEES what is repeated
        except (ValueError, TypeError):
            d["args"] = {}
        out.append(d)
    return out


def replay(spine, cfg, audit_id: int, actor) -> dict:
    """Repeat one action from the audit trail through run_tool (fresh auth/consent check)."""
    from atlas.rag import agent_tools
    row = spine.read().execute(
        "SELECT action, entity AS tool, detail AS args_json FROM audit_log WHERE id=?",
        (audit_id,)).fetchone()
    # ValueError messages below surface to the UI as HTTP 400 detail -> Croatian
    if row is None or row["action"] not in _REPLAYABLE:
        raise ValueError("audit-zapis ne postoji ili nije ponovljiv")
    if not _debounce_claim(actor.user_id, audit_id):  # idempotency: double-click not twice
        raise ValueError("ta radnja je upravo ponovljena — pričekajte prije ponovnog pokušaja")
    tool = row["tool"]
    if tool not in agent_tools.TOOLS:
        raise ValueError(f"nepoznat alat: {tool!r}")
    try:
        args = json.loads(row["args_json"] or "{}")
    except (ValueError, TypeError):
        raise ValueError("neispravni argumenti u audit-zapisu")
    result = agent_tools.run_tool(spine, cfg, actor, tool, args)  # same gate as any action
    spine.audit(actor.username, "replay", tool, row["args_json"] or "{}")
    return {"tool": tool, "result": result}
