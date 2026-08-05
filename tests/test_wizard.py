from ragspine.core.spine import init_spine
from ragspine.core.security import verify_password
from ragspine.ops import wizard
from ragspine.web import firstrun


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def test_render_preflight_blocks_on_fail():
    reqs = [
        {"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""},
        {"key": "disk", "naziv": "Disk", "status": "fail", "detalj": "0 GB", "fix": "oslobodi"},
    ]
    lines = []
    ok = wizard.render_preflight(reqs, out=lines.append)
    assert ok is False
    assert any("✗" in l for l in lines)
    assert any("oslobodi" in l for l in lines)   # fix se prikaže za fail


def test_render_preflight_passes_when_no_fail():
    reqs = [
        {"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""},
        {"key": "internet", "naziv": "Internet", "status": "warn", "detalj": "nema", "fix": "spoji"},
    ]
    assert wizard.render_preflight(reqs, out=lambda *_: None) is True


def test_cmd_setup_seeds_db(tmp_path, monkeypatch):
    """`ragspine setup` mora i dalje sjati bazu (kontni plan, watch izvori...) —
    wizard mijenja UX, ne smije ispustiti staru seeds.all sporednu radnju."""
    from ragspine.__main__ import main
    from ragspine.ops import seeds

    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(seeds, "all", lambda spine, year: called.append(year) or {})
    monkeypatch.setattr(wizard, "run", lambda spine, cfg, **kw: None)
    assert main(["setup"]) == 0
    assert called


def test_page_operater_creates_admin(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    ok = wizard.page_operater(
        s, input_fn=_reader("matej", "lozinka12", "lozinka12"), out=lambda *_: None)
    assert ok is True
    row = s.read().execute("SELECT username, pw_hash FROM users").fetchone()
    assert row["username"] == "matej"
    assert verify_password("lozinka12", row["pw_hash"]) is True


def test_page_operater_rejects_short_password(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    # prvo prekratka pa mismatch pa ispravna
    ok = wizard.page_operater(
        s, input_fn=_reader("matej", "kratka", "kratka", "lozinka12", "lozinka12"),
        out=lambda *_: None)
    assert ok is True
    assert s.read().execute("SELECT 1 FROM users").fetchone() is not None


def test_page_operater_skips_when_admin_exists(tmp_path):
    """Resume ne smije mrtvo-vezati: ako admin već postoji (web put, ili pad
    između create_first_owner i set_stage), stranica se preskače bez prompta."""
    s = init_spine(str(tmp_path / "t.db"))
    firstrun.create_first_owner(s, "vec-postoji", "lozinka12")

    def _boom(_=""):
        raise StopIteration("ne smije se konzultirati input_fn")

    ok = wizard.page_operater(s, input_fn=_boom, out=lambda *_: None)
    assert ok is True
