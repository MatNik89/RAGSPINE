from ragspine.core.spine import init_spine
from ragspine.ops import wizard_state as ws
from ragspine.web import firstrun


def _spine(tmp_path):
    return init_spine(str(tmp_path / "t.db"))


def test_stage_defaults_zero(tmp_path):
    s = _spine(tmp_path)
    assert ws.get_stage(s) == 0
    assert ws.is_complete(s) is False


def test_stage_roundtrip_and_complete(tmp_path):
    s = _spine(tmp_path)
    ws.set_stage(s, 2)
    assert ws.get_stage(s) == 2
    ws.set_stage(s, 3)  # upsert, ne duplira
    assert ws.get_stage(s) == 3
    ws.mark_complete(s)
    assert ws.is_complete(s) is True


def test_reset_clears(tmp_path):
    s = _spine(tmp_path)
    ws.set_stage(s, 4)
    ws.mark_complete(s)
    ws.reset(s)
    assert ws.get_stage(s) == 0
    assert ws.is_complete(s) is False


def test_migration_marks_complete_for_upgraded_db_with_users(tmp_path):
    """Nadogradnja postojeće instalacije: baza koja već ima korisnika (npr.
    deploy prije uvođenja wizarda, ili user kreiran preko /setup/owner a wizard
    nikad dovršen) mora nakon idućeg init() dobiti setup_complete — inače
    gatekeeper (firstrun.needs_setup) trajno zaključa postojeći deploy na
    /ui/setup nakon nadogradnje na wizard kod."""
    db_path = str(tmp_path / "t.db")
    s1 = init_spine(db_path)
    firstrun.create_first_owner(s1, "ana", "lozinka12")
    assert ws.is_complete(s1) is False   # user postoji, ali wizard ga nije označio
    s1.close()
    s2 = init_spine(db_path)             # re-init == restart nakon nadogradnje
    assert ws.is_complete(s2) is True


def test_migration_leaves_fresh_empty_db_incomplete(tmp_path):
    """Prazna baza (bez ijednog korisnika) ostaje setup_complete=False — migracija
    ne smije preskočiti fresh-install onboarding/wizard gate."""
    s = init_spine(str(tmp_path / "t.db"))
    assert ws.is_complete(s) is False
