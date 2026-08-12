"""Autonomy-ops (Paperclip steal): per-run write-cap (blast-radius), budžet
warning-prag (80%), scheduler transient-retry backoff."""
from datetime import datetime

import pytest

from atlas.business import acl, agent_budget as ab, scheduler_tasks, tenancy
from atlas.core.llm import LLMError, LLMResult
from atlas.rag import agent
from atlas.web.deps import add_user


def _actor(spine, role="member"):
    add_user(spine, "ana", "pw", role)
    return acl.Actor(user_id=1, org_id=tenancy.default_org_id(spine), role=role, username="ana")


class _SeqLLM:
    """Vrati zadani niz tool-poziva pa prazno (kraj)."""
    def __init__(self, calls):
        self.calls, self.i = list(calls), 0

    def complete(self, messages, system=None, tools=None):
        if self.i < len(self.calls):
            name, args = self.calls[self.i]; self.i += 1
            return LLMResult(text="ok", model="x", usage={}, tool_calls=[{"name": name, "args": args}])
        return LLMResult(text="gotovo", model="x", usage={}, tool_calls=[])


# ---- #8 per-run write-cap ----
def test_run_write_cap_stops_unattended(spine):
    actor = _actor(spine)
    spine.set_override("agent", "budget_run_writes", 2)
    calls = [("zapisi_belesku", {"klijent": f"K{i}", "tekst": "x"}) for i in range(3)]
    out = agent.run_agent(spine, object(), "pripremi", actor, _SeqLLM(calls),
                          max_steps=6, unattended=True, source="test")
    assert len(out["parkirano"]) == 2                 # 3. RAZLIČITA izmjena zaustavljena
    assert "limit izmjena" in out["text"]


def test_run_write_cap_zero_unlimited(spine):
    actor = _actor(spine)
    spine.set_override("agent", "budget_run_writes", 0)
    calls = [("zapisi_belesku", {"klijent": f"K{i}", "tekst": "x"}) for i in range(4)]
    out = agent.run_agent(spine, object(), "pripremi", actor, _SeqLLM(calls),
                          max_steps=8, unattended=True, source="test")
    assert len(out["parkirano"]) == 4                 # bez granice


# ---- #9 warning-prag ----
def test_usage_today_warn_at_80pct(spine):
    spine.set_override("agent", "budget_llm", 10)
    ab.consume(spine, "llm", 7)
    assert ab.usage_today(spine)["llm"]["warn"] is False   # 70%
    ab.consume(spine, "llm", 1)
    assert ab.usage_today(spine)["llm"]["warn"] is True     # 80%


def test_warn_false_when_unlimited(spine):
    spine.set_override("agent", "budget_tokens", 0)
    ab.add(spine, "tokens", 10_000_000)
    assert ab.usage_today(spine)["tokens"]["warn"] is False


# ---- #7 transient-retry ----
def _task(spine, monkeypatch, runner):
    monkeypatch.setitem(scheduler_tasks.ACTIONS, "test_fail", ("t", lambda p: None, runner))
    return scheduler_tasks.create_task(spine, 1, "t", "test_fail", {},
                                       day_of_month=None, hour=0, user="ana")


def _row(spine, sid):
    return spine.read().execute("SELECT * FROM scheduled_tasks WHERE id=?", (sid,)).fetchone()


def test_transient_error_schedules_retry(spine, monkeypatch):
    def boom(*a):
        raise LLMError("provider 503")
    sid = _task(spine, monkeypatch, boom)
    fired = scheduler_tasks.run_due(spine, None, now=datetime(2026, 1, 15, 9))
    assert fired and fired[0]["status"] == "retry"
    r = _row(spine, sid)
    assert r["retry_attempt"] == 1 and r["retry_at"] is not None


def test_permanent_error_no_retry_dead_letters(spine, monkeypatch):
    def bad(*a):
        raise ValueError("krivi parametri")           # logička greška -> NE retry
    sid = _task(spine, monkeypatch, bad)
    fired = scheduler_tasks.run_due(spine, None, now=datetime(2026, 1, 15, 9))
    assert fired[0]["status"] == "error"
    r = _row(spine, sid)
    assert r["retry_attempt"] == 0 and r["retry_at"] is None
    n = spine.read().execute("SELECT count(*) AS n FROM notifications "
                             "WHERE kind='scheduled_error'").fetchone()["n"]
    assert n == 1                                       # dead-letter


def test_retry_exhausts_then_dead_letters(spine, monkeypatch):
    def boom(*a):
        raise LLMError("503")
    sid = _task(spine, monkeypatch, boom)
    scheduler_tasks.run_due(spine, None, now=datetime(2026, 1, 15, 9))  # attempt 1
    for _ in range(5):  # gurni retry u prošlost pa ponovno fire dok se ljestvica ne iscrpi
        with spine.write() as c:
            c.execute("UPDATE scheduled_tasks SET retry_at=datetime('now','-1 minute') "
                      "WHERE id=? AND retry_at IS NOT NULL", (sid,))
        scheduler_tasks.run_due(spine, None, now=datetime(2026, 1, 15, 9))
    r = _row(spine, sid)
    assert r["retry_attempt"] == 0 and r["retry_at"] is None   # ljestvica iscrpljena, očišćeno
    n = spine.read().execute("SELECT count(*) AS n FROM notifications "
                             "WHERE kind='scheduled_error'").fetchone()["n"]
    assert n >= 1                                              # dead-letter na kraju
