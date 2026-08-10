"""A: rast registra — porezni_rokovi, kompletnost_klijenta, posalji_poruku_klijentu."""
import pytest

from atlas.business import client_visibility, kalendar, secretbox, tenancy
from atlas.business.acl import Actor
from atlas.rag import agent, agent_tools


def _actor(spine, role="member", uid=1, username="ana"):
    return Actor(user_id=uid, org_id=tenancy.default_org_id(spine), role=role, username=username)


def _client(spine, name="Pekara"):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name) VALUES(?)", (name,)).lastrowid


def test_registered():
    for t in ("porezni_rokovi", "kompletnost_klijenta"):
        assert agent_tools.TOOLS[t].readonly is True
    assert agent_tools.TOOLS["posalji_poruku_klijentu"].readonly is False


def test_porezni_rokovi(spine, cfg):
    kalendar.seed(spine, 2026)
    out = agent_tools.run_tool(spine, cfg, _actor(spine, "viewer"), "porezni_rokovi", {"dana": 3650})
    assert isinstance(out["rokovi"], list) and out["rokovi"]


def test_kompletnost_klijenta(spine, cfg):
    cid = _client(spine)
    out = agent_tools.run_tool(spine, cfg, _actor(spine, "viewer"), "kompletnost_klijenta",
                               {"klijent": "Pekara"})
    assert "score" in out and "missing" in out and out["client"] == "Pekara"


def test_kompletnost_respects_visibility(spine, cfg):
    _client(spine, "Vidljiv")
    hid = _client(spine, "Skriven")
    a = _actor(spine, "member", uid=7)
    with spine.write() as c:
        c.execute("INSERT INTO users(username,pw_hash,role,sees_all_clients) VALUES('r','x','member',0)")
    uid = spine.read().execute("SELECT id FROM users WHERE username='r'").fetchone()["id"]
    a = _actor(spine, "member", uid=uid, username="r")
    with pytest.raises(ValueError):  # skriven klijent = nepoznat
        agent_tools.run_tool(spine, cfg, a, "kompletnost_klijenta", {"klijent": "Skriven"})


def test_posalji_poruku_consent_gated(spine, cfg):
    cid = _client(spine)  # bez pristanka
    out = agent_tools.run_tool(spine, cfg, _actor(spine, "member"), "posalji_poruku_klijentu",
                               {"klijent": "Pekara", "naslov": "Bok", "tekst": "Test"})
    assert out["status"] == "skipped_no_consent"  # nema pristanka -> ne šalje
    # s pristankom -> prolazi scheme (dry_run bi bio, ali tool šalje stvarno; bez apprise = failed/sent)
    with spine.write() as c:
        c.execute("UPDATE clients SET messaging_consent=1, messaging_channel='mail', "
                  "messaging_target=? WHERE id=?", (secretbox.encrypt("mailto://a@b.com", cfg), cid))
    out2 = agent_tools.run_tool(spine, cfg, _actor(spine, "member"), "posalji_poruku_klijentu",
                                {"klijent": "Pekara", "naslov": "Bok", "tekst": "Test"})
    assert out2["status"] in ("sent", "failed")  # ovisi o apprise; scheme prošao


def test_summarize_posalji_poruku():
    s = agent.summarize_action("posalji_poruku_klijentu",
                               {"klijent": "Pekara", "naslov": "Podsjetnik"})
    assert "Pekara" in s and "Podsjetnik" in s
