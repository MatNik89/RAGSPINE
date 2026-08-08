"""Faza 5 T1: fleet tokeni + program-allowlist + red naredbi."""
import pytest

from atlas.business import devices, fleet


def _dev(spine, name="PC-Ana"):
    return devices.add_device(spine, "radna-stanica", name, user="a",
                              host="192.168.1.10")["id"]


# --- tokeni ----------------------------------------------------------------

def test_token_issue_verify_roundtrip(spine):
    did = _dev(spine)
    tok = fleet.issue_token(spine, did)
    assert tok and tok.startswith(f"{did}.")
    assert fleet.verify_token(spine, tok) == did


def test_token_rotation_invalidates_old(spine):
    did = _dev(spine)
    old = fleet.issue_token(spine, did)
    new = fleet.issue_token(spine, did)  # rotacija
    assert old != new
    assert fleet.verify_token(spine, old) is None
    assert fleet.verify_token(spine, new) == did


def test_token_revoke_makes_device_deaf(spine):
    did = _dev(spine)
    tok = fleet.issue_token(spine, did)
    fleet.revoke_token(spine, did)
    assert fleet.verify_token(spine, tok) is None


def test_verify_token_garbage_and_unknown(spine):
    assert fleet.verify_token(spine, "") is None
    assert fleet.verify_token(spine, "nema-tocke") is None
    assert fleet.verify_token(spine, "999.nepostojeci") is None
    assert fleet.verify_token(spine, "x.y") is None  # device_id nije broj


def test_issue_token_unknown_device_rejected(spine):
    with pytest.raises(ValueError):
        fleet.issue_token(spine, 4242)


# --- program allowlist -----------------------------------------------------

def test_program_add_list_remove(spine):
    fleet.add_program(spine, "Preglednik Weba", "Preglednik", user="a")
    progs = fleet.list_programs(spine)
    assert progs[0]["key"] == "preglednik_weba" and progs[0]["label"] == "Preglednik"
    fleet.remove_program(spine, "preglednik_weba")
    assert fleet.list_programs(spine) == []


def test_program_key_dedup_updates_label(spine):
    fleet.add_program(spine, "kalkulator", "Kalk", user="a")
    fleet.add_program(spine, "kalkulator", "Kalkulator", user="a")
    progs = fleet.list_programs(spine)
    assert len(progs) == 1 and progs[0]["label"] == "Kalkulator"


# --- red naredbi -----------------------------------------------------------

def test_enqueue_rejects_unknown_action(spine):
    did = _dev(spine)
    with pytest.raises(ValueError):
        fleet.enqueue(spine, did, "format_disk")


def test_enqueue_run_program_requires_existing_key(spine):
    did = _dev(spine)
    with pytest.raises(ValueError):
        fleet.enqueue(spine, did, "run_program", program_key="ne_postoji")
    fleet.add_program(spine, "kalkulator", "Kalk", user="a")
    cid = fleet.enqueue(spine, did, "run_program", program_key="kalkulator")
    assert cid


def test_next_command_order_and_status(spine):
    did = _dev(spine)
    fleet.enqueue(spine, did, "status")
    fleet.enqueue(spine, did, "shutdown")
    c1 = fleet.next_command(spine, did)
    assert c1["action"] == "status"  # najstariji prvi
    row = spine.read().execute("SELECT status FROM agent_commands WHERE id=?",
                               (c1["id"],)).fetchone()
    assert row["status"] == "in_progress"
    c2 = fleet.next_command(spine, did)
    assert c2["action"] == "shutdown"
    assert fleet.next_command(spine, did) is None  # nema više pending


def test_command_isolated_per_device(spine):
    d1, d2 = _dev(spine, "PC1"), _dev(spine, "PC2")
    fleet.enqueue(spine, d1, "status")
    assert fleet.next_command(spine, d2) is None  # tuđa naredba se ne vidi
    assert fleet.next_command(spine, d1)["action"] == "status"


def test_next_command_atomic_across_connections(tmp_path):
    # dvije Spine instance nad istom bazom ne smiju dobiti ISTU naredbu
    from atlas.core.spine import Spine
    s1 = Spine(str(tmp_path / "f.db"))
    did = devices.add_device(s1, "radna-stanica", "PC", user="a", host="192.168.1.10")["id"]
    fleet.enqueue(s1, did, "status")
    s2 = Spine(str(tmp_path / "f.db"))
    got = [fleet.next_command(s1, did), fleet.next_command(s2, did)]
    claimed = [g for g in got if g is not None]
    assert len(claimed) == 1  # točno jedna instanca dobije naredbu


def test_complete_stores_result(spine):
    did = _dev(spine)
    cid = fleet.enqueue(spine, did, "status")
    fleet.next_command(spine, did)
    fleet.complete(spine, cid, {"ok": True, "detail": "radi"})
    row = spine.read().execute("SELECT status, result FROM agent_commands WHERE id=?",
                               (cid,)).fetchone()
    assert row["status"] == "done" and "radi" in row["result"]
