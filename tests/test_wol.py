"""Faza 5 T4: WOL magic paket + chat 'kod <ime> otvori <program>'."""
import pytest

from atlas.business import devices, fleet
from atlas.core import wol


def test_magic_packet_format():
    pkt = wol.magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(pkt) == 6 + 16 * 6
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:12] == bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    assert pkt[6:] == bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF]) * 16


def test_magic_packet_accepts_dash_and_lower():
    assert wol.magic_packet("aa-bb-cc-dd-ee-ff") == wol.magic_packet("AA:BB:CC:DD:EE:FF")


def test_magic_packet_rejects_bad_mac():
    for bad in ("", "AA:BB", "ZZ:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF:00", "notamac"):
        with pytest.raises(ValueError):
            wol.magic_packet(bad)


def test_wake_fleet_only_devices_with_mac():
    sent = []
    devices_list = [
        {"name": "PC1", "mac": "AA:BB:CC:DD:EE:01"},
        {"name": "PC2", "mac": None},  # bez MAC -> preskočen
        {"name": "PC3", "mac": "AA:BB:CC:DD:EE:03"},
    ]
    woken = wol.wake_fleet(devices_list, sender=lambda pkt: sent.append(pkt))
    assert woken == ["PC1", "PC3"]
    assert len(sent) == 2


# --- chat: kod <ime> otvori <program> --------------------------------------

def _setup(spine):
    fleet.add_program(spine, "preglednik", "Preglednik", user="g")
    devices.add_device(spine, "radna-stanica", "PC-Ana", user="g", host="192.168.1.10",
                       worker_username="ana")


def test_open_on_worker_enqueues_run_program(spine):
    _setup(spine)
    res = fleet.open_on_worker(spine, "ana", "preglednik", actor_role="admin")
    assert res["ok"] is True and res["command_id"]
    row = spine.read().execute(
        "SELECT action, program_key FROM agent_commands WHERE id=?", (res["command_id"],)).fetchone()
    assert row["action"] == "run_program" and row["program_key"] == "preglednik"


def test_open_on_worker_declension_matches(spine):
    _setup(spine)
    res = fleet.open_on_worker(spine, "ane", "preglednik", actor_role="admin")  # "kod Ane"
    assert res["ok"] is True


def test_open_on_worker_requires_admin(spine):
    _setup(spine)
    res = fleet.open_on_worker(spine, "ana", "preglednik", actor_role="viewer")
    assert res["ok"] is False and "admin" in res["message"].lower()
    assert spine.read().execute("SELECT COUNT(*) n FROM agent_commands").fetchone()["n"] == 0


def test_open_on_worker_unknown_worker_or_program(spine):
    _setup(spine)
    assert fleet.open_on_worker(spine, "nitko", "preglednik", actor_role="admin")["ok"] is False
    assert fleet.open_on_worker(spine, "ana", "nepoznat", actor_role="admin")["ok"] is False
    assert spine.read().execute("SELECT COUNT(*) n FROM agent_commands").fetchone()["n"] == 0


def test_router_routes_kod_ime_otvori():
    from atlas.rag import router
    assert router.route("kod Ane otvori preglednik") == "flota"
    assert router.route("otvori dokument") != "flota"  # bez "kod <ime>" nije flota


def test_chat_lane_flota_admin_only(spine, cfg):
    from atlas.business import fleet as fl
    from atlas.rag import pipeline
    _setup(spine)
    from atlas.business.acl import Actor
    admin = Actor(user_id=1, org_id=1, role="admin", username="g")
    res = fl.flota_handle(spine, cfg, "kod ana otvori preglednik", llm=None, actor=admin)
    assert "preglednik" in res.lower() or "pokre" in res.lower()
    member = Actor(user_id=2, org_id=1, role="viewer", username="v")
    res2 = fl.flota_handle(spine, cfg, "kod ana otvori preglednik", llm=None, actor=member)
    assert "admin" in res2.lower()


def test_flota_not_cached_between_actors(spine, cfg):
    # Codex T4 fold: viewerov 'treba admin' odgovor NE smije biti keširan pa
    # serviran adminu (blokirao bi legitimno pokretanje)
    from atlas.business.acl import Actor
    from atlas.rag import pipeline
    _setup(spine)
    viewer = Actor(user_id=2, org_id=1, role="viewer", username="v")
    admin = Actor(user_id=1, org_id=1, role="admin", username="g")
    pipeline.answer(spine, cfg, "kod ana otvori preglednik", "v", llm=None, actor=viewer)
    pipeline.answer(spine, cfg, "kod ana otvori preglednik", "g", llm=None, actor=admin)
    # admin je stvarno enqueueao (nije dobio keširan viewerov odgovor)
    n = spine.read().execute(
        "SELECT COUNT(*) n FROM agent_commands WHERE action='run_program'").fetchone()["n"]
    assert n == 1
