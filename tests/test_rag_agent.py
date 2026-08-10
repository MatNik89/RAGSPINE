"""Agentska petlja (T3): read-only alati se izvrše u petlji, write alat se
predlaže (pending) i čeka potvrdu, max_steps štiti od beskonačne petlje.

ponytail: naziv datoteke je test_rag_agent.py, NE test_agent.py — potonji
već postoji i testira nepovezan atlas.browser.agent (CDP browser-use
wrapper). Brief je tražio "tests/test_agent.py", ali to bi prebrisalo
postojeće testove; ovo je namjerno odstupanje.
"""
import pytest

from atlas.business import acl, tenancy
from atlas.core.llm import LLMResult
from atlas.rag import agent
from atlas.web import deps

VALID_OIB = "10000000000"
BAD_OIB = "12345678901"


def _actor(spine, username="ana", role="member", user_id=1):
    deps.add_user(spine, username, "pw")
    org_id = tenancy.default_org_id(spine)
    return acl.Actor(user_id=user_id, org_id=org_id, role=role, username=username)


def _mk_client(spine, name="Pekara Mlinar", oib=None):
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name, oib, pdv_status, active) VALUES(?,?,?,1)",
                        (name, oib, "U sustavu PDV-a")).lastrowid
    return cid


def _call(name, args):
    return {"name": name, "args": args}


class FakeLLM:
    """Skriptirani llm: vraća LLMResult iz reda, redom, po pozivu."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def supports_tools(self):
        return True

    def complete(self, messages, system=None, model=None, max_tokens=1024,
                 temperature=0.2, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "system": system, "tools": tools})
        return self.script.pop(0)


def _result(text="", tool_calls=None):
    return LLMResult(text=text, model="fake", usage={}, tool_calls=tool_calls or [])


# --- read-only lanac pa finalni text ---------------------------------------

def test_readonly_chain_then_final_text(spine, cfg):
    _mk_client(spine, "Pekara Mlinar")
    a = _actor(spine)
    llm = FakeLLM([
        _result(tool_calls=[_call("popis_obveza", {"vrsta": "PDV"})]),
        _result(text="Evo popisa obveza za PDV."),
    ])
    out = agent.run_agent(spine, cfg, "koje obveze imamo za PDV", a, llm)
    assert out == {"text": "Evo popisa obveza za PDV.", "sources": [], "pending": None}
    assert len(llm.calls) == 2
    # rezultat alata je stigao natrag u kontekst prije drugog poziva
    assert "popis_obveza" in llm.calls[1]["messages"][-1]["content"]


def test_readonly_tool_error_reported_to_llm_not_raised(spine, cfg):
    a = _actor(spine)
    llm = FakeLLM([
        _result(tool_calls=[_call("stanje_klijenta", {"kljuc": "nepostojeci"})]),
        _result(text="Ne mogu pronaći tog klijenta."),
    ])
    out = agent.run_agent(spine, cfg, "stanje klijenta X", a, llm)
    assert out["text"] == "Ne mogu pronaći tog klijenta."
    assert "Greška" in llm.calls[1]["messages"][-1]["content"]


def test_sources_accumulated_from_pretrazi(spine, cfg):
    from atlas.docs import ingest as ing
    org_id = tenancy.default_org_id(spine)
    ing.ingest_text(spine, "Stopa PDV-a u Hrvatskoj je 25 posto.", "pdv-stope",
                    doc_type="zakon", org_id=org_id)
    a = _actor(spine)
    llm = FakeLLM([
        _result(tool_calls=[_call("pretrazi", {"upit": "kolika je stopa PDV-a"})]),
        _result(text="Stopa PDV-a je 25%."),
    ])
    out = agent.run_agent(spine, cfg, "kolika je stopa PDV-a", a, llm)
    assert out["text"] == "Stopa PDV-a je 25%."
    assert out["sources"]
    assert out["sources"][0]["doc_id"]


# --- write -> pending prekid ------------------------------------------------

def test_write_tool_builds_pending_and_stops_loop(spine, cfg):
    a = _actor(spine)
    llm = FakeLLM([
        _result(tool_calls=[_call("dodaj_klijenta", {"naziv": "Nova Firma", "oib": VALID_OIB})]),
    ])
    out = agent.run_agent(spine, cfg, "dodaj klijenta Nova Firma", a, llm)
    assert out["pending"] == {
        "tool": "dodaj_klijenta",
        "args": {"naziv": "Nova Firma", "oib": VALID_OIB},
        "summary": out["pending"]["summary"],
        "risk": "med",  # pisanje u bazu ureda = srednji tier
    }
    assert "Nova Firma" in out["pending"]["summary"]
    assert out["text"] == out["pending"]["summary"]
    assert len(llm.calls) == 1  # petlja stane odmah, ne izvršava
    # ništa nije stvarno upisano u bazu
    row = spine.read().execute("SELECT * FROM clients WHERE name=?", ("Nova Firma",)).fetchone()
    assert row is None


def test_summarize_action_dodaj_klijenta():
    s = agent.summarize_action("dodaj_klijenta", {"naziv": "Test", "oib": VALID_OIB})
    assert "Test" in s and VALID_OIB in s


def test_write_tool_invalid_args_lets_llm_fix_then_pending(spine, cfg):
    a = _actor(spine)
    llm = FakeLLM([
        _result(tool_calls=[_call("dodaj_klijenta", {"naziv": "Test", "oib": BAD_OIB})]),
        _result(tool_calls=[_call("dodaj_klijenta", {"naziv": "Test", "oib": VALID_OIB})]),
    ])
    out = agent.run_agent(spine, cfg, "dodaj klijenta Test", a, llm)
    assert out["pending"]["args"]["oib"] == VALID_OIB
    assert len(llm.calls) == 2
    assert "OIB" in llm.calls[1]["messages"][-1]["content"]


def test_write_tool_disallowed_role_no_pending(spine, cfg):
    a = _actor(spine, role="viewer")
    llm = FakeLLM([
        _result(tool_calls=[_call("dodaj_klijenta", {"naziv": "Test"})]),
        _result(text="Nemate ovlasti za tu radnju."),
    ])
    out = agent.run_agent(spine, cfg, "dodaj klijenta Test", a, llm)
    assert out["pending"] is None
    assert out["text"] == "Nemate ovlasti za tu radnju."
    assert "ovlasti" in llm.calls[1]["messages"][-1]["content"].lower()


# --- nepoznat tool -----------------------------------------------------------

def test_unknown_tool_ignored_with_message(spine, cfg):
    a = _actor(spine)
    llm = FakeLLM([
        _result(tool_calls=[_call("ne_postoji", {})]),
        _result(text="U redu, evo odgovora."),
    ])
    out = agent.run_agent(spine, cfg, "nešto", a, llm)
    assert out["text"] == "U redu, evo odgovora."
    assert len(llm.calls) == 2
    assert "Nepoznat alat" in llm.calls[1]["messages"][-1]["content"]


# --- max_steps zaštita -------------------------------------------------------

def test_max_steps_stops_infinite_loop(spine, cfg):
    _mk_client(spine, "Pekara Mlinar")
    a = _actor(spine)
    # llm uvijek traži isti readonly alat, nikad ne završi -> mora stati na max_steps
    script = [_result(tool_calls=[_call("popis_obveza", {})]) for _ in range(10)]
    llm = FakeLLM(script)
    out = agent.run_agent(spine, cfg, "obveze", a, llm, max_steps=2)
    assert len(llm.calls) == 2
    assert out["pending"] is None
    assert out["text"]  # fallback poruka, ne pad


def test_system_prompt_mentions_confirmation_rule():
    assert "potvrd" in agent.SYSTEM_PROMPT.lower()


def test_tools_filtered_by_role(spine, cfg):
    # viewer vidi SAMO readonly alate; write alati mu se ne nude (ne blefira uspjeh)
    fake = FakeLLM([_result(text="ok")])
    viewer = _actor(spine, "v", role="viewer", user_id=2)
    agent.run_agent(spine, cfg, "pitanje", viewer, fake)
    names = {t["name"] for t in fake.calls[0]["tools"]}
    assert "pretrazi" in names                      # readonly -> vidljiv
    assert "dodaj_klijenta" not in names            # write member+ -> skriven
    assert "probudi_racunalo" not in names          # admin -> skriven


def test_tools_include_writes_for_member(spine, cfg):
    fake = FakeLLM([_result(text="ok")])
    member = _actor(spine, "m", role="member", user_id=3)
    agent.run_agent(spine, cfg, "pitanje", member, fake)
    names = {t["name"] for t in fake.calls[0]["tools"]}
    assert "dodaj_klijenta" in names and "pretrazi" in names
