"""Budžet-štit: dnevni plafon na LLM-pozive / tokene / auto-write-radnje agenta.
Glavni rizik je NENADZIRANI (autonomni) run u petlji koji bi bez granice trošio
(cost-runaway) — plafon ga zaustavi graciozno. Plafoni su instalacijski-globalni
(kao model/pravila/napajanje — ATLAS je jedan ured; org-scoping config_overrides
je pre-postojeći širi zahvat). 0 = bez granice. Reset svaki dan (Europe/Zagreb).
Human-potvrđene radnje (confirm_pending) NISU budžetirane — cilj je autonomni
runaway, ne ljudske radnje."""

DEFAULTS = {"llm": 500, "tokens": 2_000_000, "writes": 200}  # velikodušno; štiti od runawaya


class BudgetError(Exception):
    """Dnevni budžet iscrpljen — agent staje graciozno (ne ruši, ne gubi podatke)."""


def _cap(spine, kind: str) -> int:
    raw = spine.get_override("agent", f"budget_{kind}", None)
    try:
        return int(raw) if raw is not None else DEFAULTS[kind]
    except (ValueError, TypeError):
        return DEFAULTS[kind]


def tokens_of(usage: dict) -> int:
    """Zbroj tokena iz sirovog provider usage-a (OpenAI total_tokens / Anthropic
    input+output). Best-effort — nepoznat oblik = 0."""
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
    """Atomično pribroji n dnevnoj potrošnji; ako prelazi plafon -> BudgetError
    (i NE pribroji). Serijalizirano write-lockom (jedan proces, više niti).
    `kind` je iz fiksne whiteliste DEFAULTS -> f-string u SQL-u nije injection."""
    if kind not in DEFAULTS:
        raise ValueError(f"nepoznata vrsta budžeta: {kind!r}")
    if n <= 0:
        return
    cap = _cap(spine, kind)
    with spine.write() as c:
        c.execute("INSERT OR IGNORE INTO agent_budget(day) VALUES(date('now','localtime'))")
        cur = c.execute(
            f"SELECT {kind} AS v FROM agent_budget WHERE day=date('now','localtime')").fetchone()
        used = (cur["v"] if cur else 0) or 0
        if cap > 0 and used + n > cap:
            raise BudgetError(f"dnevni budžet '{kind}' iscrpljen ({used}/{cap})")
        c.execute(
            f"UPDATE agent_budget SET {kind}={kind}+? WHERE day=date('now','localtime')", (n,))


def usage_today(spine) -> dict:
    row = spine.read().execute(
        "SELECT llm, tokens, writes FROM agent_budget "
        "WHERE day=date('now','localtime')").fetchone()
    used = dict(row) if row else {}
    return {k: {"used": used.get(k, 0) or 0, "cap": _cap(spine, k)} for k in DEFAULTS}
