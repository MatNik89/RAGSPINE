"""Replay: owner ponovno pokrene RANIJE IZVRŠENU agent-write-radnju iz audit-traga
(npr. 'ponovi jučerašnju kampanju'). Izvršava se kroz run_tool -> SVE provjere
ovlasti/consent/vidljivosti vrijede PONOVNO (isti gate kao svaka radnja). Owner-gated,
audit-irano kao 'replay'. Ne troši budžet (ljudska radnja, ne autonomni runaway).
Reuse audit_log — bez nove tablice."""
import json
import threading
import time

# samo zapisi gdje je entity=čisto ime alata, detail=args_json (agent confirm / auto-grant).
# parked_approve NAMJERNO izostavljen: njegov entity je 'tool:park_id' (ne čist alat).
_REPLAYABLE = ("agent_execute", "agent_auto_grant")

# idempotency-debounce (Paperclip): dvoklik/retry na POST /replay/{id} NE smije dvaput
# izvršiti računovodstveni upis. In-memory (jedan proces), kratki prozor; intencionalno
# ponovno pokretanje kasnije je i dalje moguće. ponytail: TTL-set u memoriji; upgrade =
# klijentski idempotency-token ako ikad bude više procesa.
_DEBOUNCE_S = 10.0
_recent: dict = {}  # (user_id, audit_id) -> monotonic ts
_recent_lock = threading.Lock()  # check-then-set mora biti atomičan (Codex: 2 istovremena zahtjeva)


def _debounce_claim(user_id: int, audit_id: int) -> bool:
    """True ako je ovo prvi (dopušten) poziv; False ako je duplikat unutar prozora.
    Claim se postavlja PRIJE izvršenja da spriječi istovremeni dvoklik. Cijela
    provjeri-pa-postavi je pod lockom (inače bi dva paralelna zahtjeva oba prošla)."""
    with _recent_lock:
        now = time.monotonic()
        if len(_recent) > 256:  # povremeno očisti zastarjele (bez rasta)
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
    if not _debounce_claim(actor.user_id, audit_id):  # idempotency: dvoklik ne dvostruko
        raise ValueError("ta radnja je upravo ponovljena — pričekajte prije ponovnog pokušaja")
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
