"""Rast registra (B): rokovi_isteka, dokumenti_klijenta, probudi_racunalo."""
import pytest

from atlas.business import devices, expiry, tenancy
from atlas.business.acl import Actor
from atlas.rag import agent_tools


def _actor(spine, role="member", uid=1, username="ana"):
    return Actor(user_id=uid, org_id=tenancy.default_org_id(spine), role=role, username=username)


def _client(spine, name="Pekara"):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name) VALUES(?)", (name,)).lastrowid


def _restricted_actor(spine, username="r"):
    with spine.write() as c:
        c.execute("INSERT INTO users(username,pw_hash,role,sees_all_clients) "
                  "VALUES(?,'x','member',0)", (username,))
    uid = spine.read().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    return _actor(spine, "member", uid=uid, username=username), uid


def test_registered():
    assert agent_tools.TOOLS["rokovi_isteka"].readonly is True
    assert agent_tools.TOOLS["dokumenti_klijenta"].readonly is True
    t = agent_tools.TOOLS["probudi_racunalo"]
    assert t.readonly is False and t.min_role == "admin"


def test_rokovi_isteka(spine, cfg):
    cid = _client(spine)
    expiry.add(spine, cid, "osobna", "Osobna iskaznica", "2026-09-01")
    out = agent_tools.run_tool(spine, cfg, _actor(spine, "viewer"), "rokovi_isteka", {"dana": 3650})
    assert any(r["client_id"] == cid for r in out["rokovi"])


def test_rokovi_isteka_respects_visibility(spine, cfg):
    vis = _client(spine, "Vidljiv")
    hid = _client(spine, "Skriven")
    expiry.add(spine, vis, "osobna", "OI", "2026-09-01")
    expiry.add(spine, hid, "osobna", "OI", "2026-09-01")
    a, uid = _restricted_actor(spine)
    from atlas.business import client_visibility
    client_visibility.grant(spine, uid, vis, "sys")  # vidi samo Vidljiv
    out = agent_tools.run_tool(spine, cfg, a, "rokovi_isteka", {"dana": 3650})
    ids = {r["client_id"] for r in out["rokovi"]}
    assert vis in ids and hid not in ids  # skriveni klijent ne curi


def test_dokumenti_klijenta(spine, cfg):
    cid = _client(spine)
    with spine.write() as c:
        c.execute("INSERT INTO documents(title, doc_type, client_id, sha256) "
                  "VALUES('Ugovor.pdf','ugovor',?, 'h1')", (cid,))
    out = agent_tools.run_tool(spine, cfg, _actor(spine, "viewer"), "dokumenti_klijenta",
                               {"klijent": "Pekara"})
    assert out["klijent"] == "Pekara"
    assert [d["title"] for d in out["dokumenti"]] == ["Ugovor.pdf"]


def test_dokumenti_respects_visibility(spine, cfg):
    _client(spine, "Vidljiv")
    _client(spine, "Skriven")
    a, _ = _restricted_actor(spine)
    with pytest.raises(ValueError):  # skriven = nepoznat
        agent_tools.run_tool(spine, cfg, a, "dokumenti_klijenta", {"klijent": "Skriven"})


def test_probudi_racunalo_needs_admin(spine, cfg):
    devices.add_device(spine, "radna-stanica", "PC-Ana", user="g",
                       mac="AA:BB:CC:DD:EE:FF", worker_username="ana")
    # member nema ovlast -> run_tool odbija prije izvršenja
    with pytest.raises(ValueError):
        agent_tools.run_tool(spine, cfg, _actor(spine, "member"), "probudi_racunalo",
                             {"radnik": "ana"})


def test_probudi_racunalo_sends(spine, cfg):
    devices.add_device(spine, "radna-stanica", "PC-Ana", user="g",
                       mac="AA:BB:CC:DD:EE:FF", worker_username="ana")
    sent = []
    from atlas.business import fleet
    res = fleet.wake_worker(spine, "ana", actor_role="admin", sender=lambda pkt: sent.append(pkt))
    assert res["ok"] and len(sent) == 1  # magic paket poslan


def test_probudi_racunalo_ambiguous(spine, cfg):
    from atlas.business import fleet
    res = fleet.wake_worker(spine, "ana", actor_role="admin", sender=lambda pkt: None)
    assert res["ok"] is False  # nema stanice s MAC-om -> jasna poruka
