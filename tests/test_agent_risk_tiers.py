"""Risk-tieri alata (OpenWorker 4-tier / Abu Plan Mode severity): svaki alat
ima rizik low|med|high. readonly=low; vanjska nuspojava (poruka klijentu,
pokretanje/buđenje stanice, uči s weba)=high; ostali write=med. Pending
prijedlog nosi rizik da UI/Telegram prikažu jačinu i traže jaču potvrdu za high.
Nepoznat alat = high (fail-safe: neklasificirano tretiramo najopreznije)."""
from atlas.business import acl, tenancy
from atlas.core.llm import LLMResult
from atlas.rag import agent, agent_tools
from atlas.web import deps

EXTERNAL_HIGH = {"posalji_poruku_klijentu", "pokreni_program",
                 "probudi_racunalo", "nauci_izvor"}


def _actor(spine, username="ana", role="member", user_id=1):
    deps.add_user(spine, username, "pw")
    return acl.Actor(user_id=user_id, org_id=tenancy.default_org_id(spine),
                     role=role, username=username)


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)

    def supports_tools(self):
        return True

    def complete(self, messages, system=None, model=None, max_tokens=1024,
                 temperature=0.2, tools=None):
        return self.script.pop(0)


def _result(tool_calls=None):
    return LLMResult(text="", model="fake", usage={}, tool_calls=tool_calls or [])


def test_readonly_tools_are_low_risk():
    for name, t in agent_tools.TOOLS.items():
        if t.readonly:
            assert agent_tools.risk(name) == "low", f"{name}: readonly ali nije low"


def test_external_effect_tools_are_high_risk():
    for name in EXTERNAL_HIGH:
        assert agent_tools.risk(name) == "high", f"{name}: bi trebao biti high"


def test_plain_write_is_med_risk():
    assert agent_tools.risk("dodaj_klijenta") == "med"
    assert agent_tools.risk("oznaci_obvezu") == "med"


def test_unknown_tool_is_high_risk_failsafe():
    assert agent_tools.risk("ne_postoji") == "high"


def test_high_risk_tools_are_never_readonly():
    for name, t in agent_tools.TOOLS.items():
        if agent_tools.risk(name) == "high":
            assert not t.readonly, f"{name}: high-risk ne smije biti readonly"


def test_pending_proposal_carries_risk(spine, cfg):
    a = _actor(spine)
    llm = FakeLLM([_result(tool_calls=[{
        "name": "posalji_poruku_klijentu",
        "args": {"klijent": "X", "naslov": "Podsjetnik", "tekst": "Molimo dokumente."}}])])
    out = agent.run_agent(spine, cfg, "javi klijentu X", a, llm)
    assert out["pending"]["risk"] == "high"
