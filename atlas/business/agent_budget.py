"""Budget guard: daily cap on agent LLM calls / tokens / auto-write actions.
The main risk is an UNATTENDED (autonomous) run in a loop that would spend without
limit (cost-runaway) — the cap stops it gracefully. Caps are installation-global
(like model/rules/power — ATLAS is a single office; org-scoping config_overrides is
a pre-existing larger change). 0 = unlimited. Resets daily (Europe/Zagreb).
Human-confirmed actions (confirm_pending) are NOT budgeted — the target is autonomous
runaway, not human actions."""

DEFAULTS = {"llm": 500, "tokens": 2_000_000, "writes": 200}  # generous; guards against runaway
WARN_FRACTION = 0.8            # warn at 80% of the daily cap (before hard-stop; Paperclip warnPercent)
RUN_WRITE_CAP_DEFAULT = 20     # max distinct writes per SINGLE run (blast-radius; Paperclip)


class BudgetError(Exception):
    """Daily budget exhausted — agent stops gracefully (no crash, no data loss)."""


def _cap(spine, kind: str) -> int:
    raw = spine.get_override("agent", f"budget_{kind}", None)
    try:
        return int(raw) if raw is not None else DEFAULTS[kind]
    except (ValueError, TypeError):
        return DEFAULTS[kind]


def tokens_of(usage: dict) -> int:
    """Sum tokens from the raw provider usage (OpenAI total_tokens / Anthropic
    input+output). Best-effort — unknown shape = 0."""
    if not isinstance(usage, dict):
        return 0
    if usage.get("total_tokens") is not None:
        try:
            return int(usage["total_tokens"])
        except (ValueError, TypeError):
            return 0
    a = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    b = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    try:
        return int(a) + int(b)
    except (ValueError, TypeError):
        return 0


def consume(spine, kind: str, n: int = 1) -> None:
    """Atomically add n to today's usage; if it exceeds the cap -> BudgetError
    (and does NOT add). Serialized by the write-lock (single process, multiple threads).
    `kind` comes from the fixed DEFAULTS whitelist -> the f-string in SQL is not injection."""
    if kind not in DEFAULTS:
        raise ValueError(f"unknown budget kind: {kind!r}")
    if n <= 0:
        return
    cap = _cap(spine, kind)
    with spine.write() as c:
        c.execute("INSERT OR IGNORE INTO agent_budget(day) VALUES(date('now','localtime'))")
        cur = c.execute(
            f"SELECT {kind} AS v FROM agent_budget WHERE day=date('now','localtime')").fetchone()
        used = (cur["v"] if cur else 0) or 0
        if cap > 0 and used + n > cap:
            # user-facing (surfaces in the chat answer as [Zaustavljeno: ...]) -> Croatian
            raise BudgetError(f"dnevni budžet '{kind}' iscrpljen ({used}/{cap})")
        c.execute(
            f"UPDATE agent_budget SET {kind}={kind}+? WHERE day=date('now','localtime')", (n,))


def add(spine, kind: str, n: int) -> None:
    """Always add usage (no cap check, does NOT raise). For measurements that happen
    AFTER the cost (tokens: the call already ran) — they must be recorded even when
    over the cap, otherwise the total never reaches the cap and the door never closes
    (Codex: token-bypass). The cap is then checked BEFORE the next call via over()."""
    if kind not in DEFAULTS:
        raise ValueError(f"unknown budget kind: {kind!r}")
    if n <= 0:
        return
    with spine.write() as c:
        c.execute("INSERT OR IGNORE INTO agent_budget(day) VALUES(date('now','localtime'))")
        c.execute(
            f"UPDATE agent_budget SET {kind}={kind}+? WHERE day=date('now','localtime')", (n,))


def over(spine, kind: str) -> bool:
    """Has today's usage already reached the cap (cap>0)? Pre-check before a call."""
    if kind not in DEFAULTS:
        raise ValueError(f"unknown budget kind: {kind!r}")
    cap = _cap(spine, kind)
    if cap <= 0:
        return False
    row = spine.read().execute(
        f"SELECT {kind} AS v FROM agent_budget WHERE day=date('now','localtime')").fetchone()
    return ((row["v"] if row else 0) or 0) >= cap


def usage_today(spine) -> dict:
    row = spine.read().execute(
        "SELECT llm, tokens, writes FROM agent_budget "
        "WHERE day=date('now','localtime')").fetchone()
    used = dict(row) if row else {}
    out = {}
    for k in DEFAULTS:
        u, cap = used.get(k, 0) or 0, _cap(spine, k)
        out[k] = {"used": u, "cap": cap,
                  "warn": bool(cap > 0 and u >= cap * WARN_FRACTION)}  # 80% -> warning
    return out


def run_write_cap(spine) -> int:
    """Max distinct write actions per single agent run (0 = unlimited)."""
    raw = spine.get_override("agent", "budget_run_writes", None)
    try:
        return int(raw) if raw is not None else RUN_WRITE_CAP_DEFAULT
    except (ValueError, TypeError):
        return RUN_WRITE_CAP_DEFAULT
