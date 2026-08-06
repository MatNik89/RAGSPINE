from ragspine.core.spine import init_spine
from ragspine.core.security import verify_password
from ragspine.ops import wizard, wizard_state as ws
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


def test_run_handles_eof_without_traceback(spine, cfg, monkeypatch):
    """Piped stdin / servis bez terminala (npr. `ragspine setup < /dev/null`)
    ne smije dati traceback — samo kratku uputu; resume ostaje na zadnjem
    dovršenom koraku. Preflight je mockan da ne udara na mrežu (Ollama/net)."""
    ok_reqs = [{"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""}]
    monkeypatch.setattr(wizard.preflight, "requirements", lambda cfg: ok_reqs)

    def _eof(_=""):
        raise EOFError()

    lines = []
    wizard.run(spine, cfg, input_fn=_eof, out=lines.append)  # ne smije propagirati EOFError
    assert any("interaktivni terminal" in l for l in lines)
    assert ws.get_stage(spine) == 1     # preduvjeti (svi ok) prošli, operater prekinut EOF-om
    assert ws.is_complete(spine) is False


def test_run_success_marks_setup_complete(spine, cfg, monkeypatch):
    """P1 wizard end-to-end (preduvjeti OK + operater kreiran) mora postaviti
    setup_complete — inače gatekeeper (firstrun.needs_setup) trajno zaključava
    web sučelje na /ui/setup i za instalacije koje su upravo dovršile wizard."""
    ok_reqs = [{"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""}]
    monkeypatch.setattr(wizard.preflight, "requirements", lambda cfg: ok_reqs)
    lines = []
    wizard.run(spine, cfg, input_fn=_reader("matej", "lozinka12", "lozinka12"), out=lines.append)
    assert ws.get_stage(spine) == 2
    assert ws.is_complete(spine) is True
    assert firstrun.needs_setup(spine) is False


def test_choose_embed_model_bge_when_ram_allows():
    st = {"ram_total_gb": 16.0}
    assert wizard.choose_embed_model(st, "mali-default") == "BAAI/bge-m3"


def test_choose_embed_model_fallback_on_small_ram():
    st = {"ram_total_gb": 2.0}   # 1.2/2.0 = 60% -> tight, ne "fits"
    assert wizard.choose_embed_model(st, "mali-default") == "mali-default"


def test_setup_embedding_falls_back_on_download_error(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    from ragspine.config import Config, set_config
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    set_config(cfg)
    calls = []

    def _fake_download(c):
        calls.append(c.embed_model)
        if c.embed_model == "BAAI/bge-m3":
            return {"ok": False, "error": "ne stane"}
        return {"ok": True, "model": c.embed_model, "dim": 384}

    monkeypatch.setattr(wizard, "_download_embed", _fake_download)
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ram_total_gb": 16.0})
    got = wizard.setup_embedding(s, cfg, out=lambda *_: None)
    assert got == cfg.embed_model          # fallback na default
    assert calls == ["BAAI/bge-m3", cfg.embed_model]
    set_config(None)


class _FakeRes:
    def __init__(self, text):
        self.text = text
        self.model = "test-model"


def test_self_test_ok_on_nonempty_answer(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "_llm_complete", lambda spine, cfg, prompt: _FakeRes("OK RAGSPINE"))
    lines = []
    assert wizard.self_test(s, None, input_fn=_reader(), out=lines.append) is True
    assert not any("upozorenje" in l.lower() for l in lines)


def test_self_test_soft_warns_on_wrong_text(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "_llm_complete", lambda spine, cfg, prompt: _FakeRes("bok!"))
    lines = []
    assert wizard.self_test(s, None, input_fn=_reader(), out=lines.append) is True
    assert any("upozorenje" in l.lower() for l in lines)


def test_self_test_retries_then_user_gives_up(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    calls = []

    def _fail(spine, cfg, prompt):
        calls.append(1)
        raise wizard.LLMUnavailable("hladno")

    monkeypatch.setattr(wizard, "_llm_complete", _fail)
    # dva puta "da, pokusaj ponovno", pa "ne" -> False; ukupno 3 poziva LLM-a
    ok = wizard.self_test(s, None, input_fn=_reader("da", "da", "ne"),
                          out=lambda *_: None)
    assert ok is False
    assert len(calls) == 3


def _fits_16gb():
    return wizard.preflight.model_fits(state={"ram_total_gb": 16.0, "vram_gb": 0.0})


def test_render_model_catalog_marks_recommendation():
    fits = _fits_16gb()
    rec = wizard.preflight.recommend_chat_model(fits)
    lines = []
    names = wizard.render_model_catalog(fits, rec, out=lines.append)
    assert rec in names
    assert any("PREPORUKA" in l for l in lines)
    assert any("🟢" in l or "🟡" in l for l in lines)


def test_page_model_skip_branch_when_ollama_unavailable(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (False, "nema"))
    monkeypatch.setattr(wizard.preflight, "start_ollama", lambda **k: False)
    # "da" na "preskoci i postavi kasnije?"
    ok = wizard.page_model(s, None, input_fn=_reader("da"), out=lambda *_: None)
    assert ok is True


def test_page_model_full_happy_path(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    from ragspine.business import model_settings
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (True, "radi"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url=None: "0.6.0")
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ram_total_gb": 16.0, "vram_gb": 0.0})
    pulled = []
    monkeypatch.setattr(wizard.preflight, "ollama_pull",
                        lambda name, url=None, out=print: pulled.append(name) or True)
    monkeypatch.setattr(wizard, "setup_embedding", lambda sp, c, out=print: "emb-model")
    monkeypatch.setattr(wizard, "self_test", lambda sp, c, **k: True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "def-emb"
    # "" = prihvati default (preporuceni model je predodabran u prompt_choice)
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader(""), out=lambda *_: None)
    assert ok is True
    assert len(pulled) == 1
    saved = model_settings.get(s)
    assert saved["provider"] == "ollama"
    assert saved["model"] == pulled[0]
    assert saved["embed_model"] == "emb-model"
