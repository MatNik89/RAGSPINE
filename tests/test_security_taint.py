"""Branch B (Paperclip security): trust-taint (untrusted sadržaj degradira auto-grant
+ blokira samo-modifikaciju), value-based redakcija tajni, key-fingerprint."""
from atlas.business import acl, agent_grants, secretbox, tenancy
from atlas.core import security
from atlas.core.llm import LLMResult
from atlas.rag import agent, agent_tools
from atlas.web.deps import add_user


def _actor(spine, role="member"):
    add_user(spine, "ana", "pw", role)
    return acl.Actor(user_id=1, org_id=tenancy.default_org_id(spine), role=role, username="ana")


class _SeqLLM:
    def __init__(self, calls):
        self.calls, self.i = list(calls), 0

    def complete(self, messages, system=None, tools=None):
        if self.i < len(self.calls):
            n, a = self.calls[self.i]; self.i += 1
            return LLMResult(text="ok", model="x", usage={}, tool_calls=[{"name": n, "args": a}])
        return LLMResult(text="gotovo", model="x", usage={}, tool_calls=[])


# ---- #2 trust-taint ----
def test_grant_autoexec_when_not_tainted(spine, monkeypatch):
    """Kontrola: bez čitanja dokumenata, grant auto-izvrši."""
    actor = _actor(spine)
    agent_grants.create_grant(spine, actor, "zapisi_belesku", {"klijent": "K"}, user="ana")
    monkeypatch.setattr(agent_tools, "run_tool", lambda s, c, a, n, ar: {"ok": 1})
    out = agent.run_agent(spine, object(), "x", actor,
                          _SeqLLM([("zapisi_belesku", {"klijent": "K", "tekst": "y"})]),
                          max_steps=4, unattended=True, source="t")
    assert out["izvrseno"] == ["zapisi_belesku"] and out["parkirano"] == []


def test_taint_downgrades_autogrant_to_park(spine, monkeypatch):
    """Nakon pretrazi (tainting), isti grant se NE auto-izvrši -> parkira se."""
    actor = _actor(spine)
    agent_grants.create_grant(spine, actor, "zapisi_belesku", {"klijent": "K"}, user="ana")
    monkeypatch.setattr(agent_tools, "run_tool", lambda s, c, a, n, ar: {"ok": 1})
    out = agent.run_agent(spine, object(), "x", actor,
                          _SeqLLM([("pretrazi", {"upit": "nešto"}),
                                   ("zapisi_belesku", {"klijent": "K", "tekst": "y"})]),
                          max_steps=5, unattended=True, source="t")
    assert out["izvrseno"] == []                  # tainted -> nije auto
    assert len(out["parkirano"]) == 1             # degradirano na parkiranje


def test_taint_blocks_self_modification(spine, monkeypatch):
    """Nakon čitanja dokumenata, predlozi_vjestinu/nauci_izvor se ODBIJU (ni ne parkiraju)."""
    actor = _actor(spine)
    monkeypatch.setattr(agent_tools, "run_tool", lambda s, c, a, n, ar: {"ok": 1})
    out = agent.run_agent(spine, object(), "x", actor,
                          _SeqLLM([("pretrazi", {"upit": "zli dokument"}),
                                   ("predlozi_vjestinu", {"ime": "hack", "koraci": "..."})]),
                          max_steps=5, unattended=True, source="t")
    assert out["parkirano"] == [] and out["izvrseno"] == []   # samo-modifikacija odbijena


# ---- #5 value-based redakcija ----
def test_redact_secret_values_longest_first():
    t = "greška: auth failed s tokenom abcdef123456 (pokušaj abcdef)"
    r = security.redact_secret_values(t, ["abcdef", "abcdef123456"])
    assert "abcdef123456" not in r and "[REDACTED]" in r


def test_redact_ignores_short_values():
    assert security.redact_secret_values("vrijednost abc ostaje", ["abc"]) == "vrijednost abc ostaje"


def test_redact_empty_safe():
    assert security.redact_secret_values("", ["x"]) == ""
    assert security.redact_secret_values("tekst", [None, "", "  "]).strip() == "tekst"


# ---- #14 key-fingerprint ----
def test_key_fingerprint_stable_and_hidden():
    class C:
        jwt_secret = "supersecret-vrijednost-123"
    fp = secretbox.key_fingerprint(C())
    assert len(fp) == 12 and fp == secretbox.key_fingerprint(C())
    assert "supersecret" not in fp

    class E:
        jwt_secret = ""
    assert secretbox.key_fingerprint(E()) == ""
