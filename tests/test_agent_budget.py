"""Budžet-štit: dnevni plafon LLM-poziva/tokena/auto-write-a; štiti od
cost-runawaya (pos. autonomni run). 0 = bez granice. Human-radnje nisu budžetirane."""
import pytest

from atlas.business import agent_budget as ab


def test_consume_increments_and_reports(spine):
    ab.consume(spine, "llm", 3)
    ab.consume(spine, "llm", 2)
    u = ab.usage_today(spine)
    assert u["llm"]["used"] == 5
    assert u["llm"]["cap"] == ab.DEFAULTS["llm"]


def test_cap_blocks_over_and_does_not_consume(spine):
    spine.set_override("agent", "budget_llm", 4)
    ab.consume(spine, "llm", 4)          # točno na plafonu -> prolazi
    with pytest.raises(ab.BudgetError):
        ab.consume(spine, "llm", 1)      # +1 preko -> blok
    assert ab.usage_today(spine)["llm"]["used"] == 4  # neuspjeh NIJE pribrojen


def test_zero_cap_is_unlimited(spine):
    spine.set_override("agent", "budget_writes", 0)
    for _ in range(50):
        ab.consume(spine, "writes", 100)
    assert ab.usage_today(spine)["writes"]["used"] == 5000


def test_tokens_of_both_provider_shapes():
    assert ab.tokens_of({"total_tokens": 42}) == 42
    assert ab.tokens_of({"input_tokens": 10, "output_tokens": 7}) == 17      # Anthropic
    assert ab.tokens_of({"prompt_tokens": 3, "completion_tokens": 4}) == 7   # OpenAI
    assert ab.tokens_of({}) == 0
    assert ab.tokens_of(None) == 0


def test_unknown_kind_rejected(spine):
    with pytest.raises(ValueError):
        ab.consume(spine, "llm; DROP TABLE agent_budget", 1)  # ne u whitelisti -> odbijeno


def test_bad_override_falls_back_to_default(spine):
    spine.set_override("agent", "budget_llm", "nije-broj")
    assert ab.usage_today(spine)["llm"]["cap"] == ab.DEFAULTS["llm"]


def test_add_persists_even_over_cap_and_over_detects(spine):
    """Token-bypass fix (Codex): add() bilježi i preko plafona -> total dosegne
    plafon -> over() zatvori vrata. (consume() bi rollbackao over-cap i vrata
    se nikad ne zatvore.)"""
    spine.set_override("agent", "budget_tokens", 1000)
    ab.add(spine, "tokens", 800)
    assert ab.over(spine, "tokens") is False
    ab.add(spine, "tokens", 800)          # 1600 > 1000, ali SE BILJEŽI
    assert ab.usage_today(spine)["tokens"]["used"] == 1600
    assert ab.over(spine, "tokens") is True


def test_over_zero_cap_never(spine):
    spine.set_override("agent", "budget_tokens", 0)
    ab.add(spine, "tokens", 10_000)
    assert ab.over(spine, "tokens") is False


def test_run_agent_stops_when_tokens_exhausted(spine):
    from atlas.business import acl, tenancy
    from atlas.rag import agent
    from atlas.web.deps import add_user
    add_user(spine, "ana", "pw", "member")
    actor = acl.Actor(user_id=1, org_id=tenancy.default_org_id(spine),
                      role="member", username="ana")
    spine.set_override("agent", "budget_tokens", 500)
    ab.add(spine, "tokens", 500)  # već na plafonu

    class _LLM:
        def complete(self, *a, **k):
            raise AssertionError("LLM ne smije biti pozvan kad su tokeni iscrpljeni")

    out = agent.run_agent(spine, object(), "pitanje", actor, _LLM(), max_steps=3)
    assert "tokens" in out["text"] and "Zaustavljeno" in out["text"]


def test_run_agent_stops_on_llm_budget(spine):
    """run_agent staje graciozno kad je LLM-budžet iscrpljen (ne ruši)."""
    from atlas.business import acl, tenancy
    from atlas.core.llm import LLMResult
    from atlas.rag import agent
    from atlas.web.deps import add_user
    add_user(spine, "ana", "pw", "member")
    actor = acl.Actor(user_id=1, org_id=tenancy.default_org_id(spine),
                      role="member", username="ana")
    spine.set_override("agent", "budget_llm", 0)  # najprije napuni potrošnju do plafona 2
    spine.set_override("agent", "budget_llm", 2)
    ab.consume(spine, "llm", 2)  # potrošeno 2/2

    class _LLM:
        def complete(self, *a, **k):
            raise AssertionError("LLM ne smije biti pozvan kad je budžet iscrpljen")

    out = agent.run_agent(spine, object(), "pitanje", actor, _LLM(), max_steps=3)
    assert "Zaustavljeno" in out["text"]
