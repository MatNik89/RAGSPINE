from atlas.core.spine import init_spine
from atlas.core.security import verify_password
from atlas.ops import wizard, wizard_state as ws
from atlas.web import firstrun


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def _mreza_mocks(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard.preflight, "local_ip", lambda: "192.168.1.7")
    monkeypatch.setattr(wizard.preflight, "port_free", lambda h, p: True)
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ip_mode": "static"})
    monkeypatch.setattr(wizard.certs, "generate_self_signed",
                        lambda d, ips, hostnames=None, out=print: (str(tmp_path / "cert.pem"),
                                                                   str(tmp_path / "key.pem")))
    monkeypatch.setattr(wizard.certs, "fingerprint_sha256", lambda p: "AA:BB")


def test_page_mreza_happy_path_saves_overrides(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    _mreza_mocks(monkeypatch, tmp_path)

    class _Cfg:
        data_dir = str(tmp_path)
    # izbori: "1" (detektirani IP), port Enter (default 8443), proxy Enter (bez),
    # servis "ne"
    ok = wizard.page_mreza(s, _Cfg(), input_fn=_reader("1", "", "", "ne"),
                           out=lambda *_: None)
    assert ok is True
    assert s.get_override("net", "host") == "192.168.1.7"
    assert s.get_override("net", "port") == "8443"
    assert s.get_override("net", "cert_path").endswith("cert.pem")
    assert s.get_override("net", "key_path").endswith("key.pem")


def test_page_mreza_busy_port_retries(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    _mreza_mocks(monkeypatch, tmp_path)
    ports = iter([False, True])                     # prvi zauzet, drugi slobodan
    monkeypatch.setattr(wizard.preflight, "port_free", lambda h, p: next(ports))

    class _Cfg:
        data_dir = str(tmp_path)
    lines = []
    ok = wizard.page_mreza(s, _Cfg(), input_fn=_reader("1", "8443", "9000", "", "ne"),
                           out=lines.append)
    assert ok is True
    assert s.get_override("net", "port") == "9000"
    assert any("zauzet" in l.lower() for l in lines)


def test_page_mape_preskok(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    ok = wizard.page_mape(s, None, input_fn=_reader("n"), out=lambda *_: None)
    assert ok is True
    from atlas.business import folders
    assert folders.list_folders(s) == []


def test_page_mape_registrira_mapu(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    d = tmp_path / "klijenti"
    d.mkdir()
    # "d" (poveži), putanja za Klijente, prazno za preostale 3 uloge
    lines = []
    ok = wizard.page_mape(s, None, input_fn=_reader("d", str(d), "", "", ""),
                          out=lines.append)
    assert ok is True
    from atlas.business import folders
    rows = folders.list_folders(s)
    assert len(rows) == 1 and rows[0]["role"] == "klijenti"
    assert any("ATLAS_MOUNT_ROOTS" in l for l in lines)


def test_page_mape_nedostupna_pa_odustane(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    # UNC putanja — trebala bi biti nedostupna i očekujemo net use hint
    bad_unc = r"\\nas\share\nema"
    # "d", nepostojeća UNC putanja, "n" (ne pokušava ponovno), prazno za ostale 3
    lines = []
    ok = wizard.page_mape(s, None, input_fn=_reader("d", bad_unc, "n", "", "", ""),
                          out=lines.append)
    assert ok is True
    assert any("net use" in l for l in lines)
    from atlas.business import folders
    assert folders.list_folders(s) == []


def test_page_mape_ne_unc_putanja_nema_net_use(tmp_path):
    """Ne-UNC (npr. /nema/takve/mape) nepostojeća putanja — očekuj 'nije UNC',
    NEMA 'net use' hint-a."""
    s = init_spine(str(tmp_path / "t.db"))
    bad_local = "/nema/takve/mape"
    lines = []
    ok = wizard.page_mape(s, None, input_fn=_reader("d", bad_local, "n", "", "", ""),
                          out=lines.append)
    assert ok is True
    text = "\n".join(lines)
    assert "nije UNC" in text
    assert "net use" not in text.lower()


def test_page_mape_upozorava_na_slovo_pogona(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    # "d", drive-letter putanja (ne postoji na Linuxu), "n", prazno za ostale 3
    lines = []
    wizard.page_mape(s, None, input_fn=_reader("d", "Z:\\skenovi", "n", "", "", ""),
                     out=lines.append)
    assert any("Slovo pogona" in l for l in lines)


def test_net_use_hint_iz_unc_putanje():
    hint = wizard._net_use_hint(r"\\nas\ured\klijenti\2026")
    assert r"net use \\nas\ured" in hint
    assert "*" in hint and "/persistent:no" in hint


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


def test_page_preduvjeti_offers_winget_install_on_windows(monkeypatch):
    """fail na Windowsu → radiolist nudi Auto-instaliraj; nakon pokušaja
    ponovno provjeri preduvjete."""
    reqs_fail = [{"key": "tesseract", "naziv": "OCR", "status": "fail",
                  "detalj": "nije pronađen", "fix": "winget install ..."}]
    reqs_ok = [{"key": "tesseract", "naziv": "OCR", "status": "ok", "detalj": "ok", "fix": ""}]
    seq = iter([reqs_fail, reqs_ok])
    monkeypatch.setattr(wizard.preflight, "requirements", lambda cfg: next(seq))
    monkeypatch.setattr(wizard.os, "name", "nt")
    calls = []
    monkeypatch.setattr(wizard.preflight, "install_via_winget",
                        lambda key, out=print: calls.append(key) or True)
    # radiolist (fail, Windows): 1=Auto-instaliraj: OCR, 2=Provjeri ponovno, 3=Prekini
    ok = wizard.page_preduvjeti(None, None, input_fn=_reader("1"), out=lambda *_: None)
    assert ok is True
    assert calls == ["tesseract"]


def test_page_preduvjeti_no_winget_offer_on_non_windows(monkeypatch):
    """Izvan Windowsa nema Auto-instaliraj stavke; Prekini vraća False."""
    reqs_fail = [{"key": "tesseract", "naziv": "OCR", "status": "fail",
                  "detalj": "nije pronađen", "fix": "apt install ..."}]
    monkeypatch.setattr(wizard.preflight, "requirements", lambda cfg: reqs_fail)
    monkeypatch.setattr(wizard.os, "name", "posix")
    lines = []
    # radiolist: 1=Provjeri ponovno, 2=Prekini setup
    ok = wizard.page_preduvjeti(None, None, input_fn=_reader("2"), out=lines.append)
    assert ok is False
    assert not any("Auto-instaliraj" in l for l in lines)


def test_cmd_setup_seeds_db(tmp_path, monkeypatch):
    """`atlas setup` mora i dalje sjati bazu (kontni plan, watch izvori...) —
    wizard mijenja UX, ne smije ispustiti staru seeds.all sporednu radnju."""
    from atlas.__main__ import main
    from atlas.ops import seeds

    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
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
    """Piped stdin / servis bez terminala (npr. `atlas setup < /dev/null`)
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
    """P4 wizard end-to-end trebao bi dosegnuti stage 6 (page_gotovo) i završiti.
    page_model/mreza/mape/gotovo su mockani da ne diraju mrežu/Ollama."""
    ok_reqs = [{"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""}]
    monkeypatch.setattr(wizard.preflight, "requirements", lambda cfg: ok_reqs)
    monkeypatch.setattr(wizard, "page_model", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_mreza", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_mape", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_gotovo", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "launch_now", lambda *a, **k: None)
    lines = []
    wizard.run(spine, cfg, input_fn=_reader("matej", "lozinka12", "lozinka12"), out=lines.append)
    assert ws.get_stage(spine) == 6
    assert ws.is_complete(spine) is True
    assert firstrun.needs_setup(spine) is False


def test_run_reaches_stage6_and_completes(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    from atlas.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    for p in ("page_preduvjeti", "page_operater", "page_model", "page_mreza", "page_mape", "page_gotovo"):
        monkeypatch.setattr(wizard, p, lambda *a, **k: True)
    monkeypatch.setattr(wizard, "launch_now", lambda *a, **k: None)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert ws.get_stage(s) == 6
    assert ws.is_complete(s) is True


def test_run_no_complete_when_model_page_cancelled(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    from atlas.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "page_preduvjeti", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_operater", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_model", lambda *a, **k: False)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert ws.get_stage(s) == 2          # stranice 1-2 prosle
    assert ws.is_complete(s) is False    # model otkazan -> nije complete


def test_run_no_complete_when_mreza_page_cancelled(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    from atlas.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "page_preduvjeti", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_operater", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_model", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_mreza", lambda *a, **k: False)
    monkeypatch.setattr(wizard, "page_mape", lambda *a, **k: True)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert ws.get_stage(s) == 3          # stranice 1-3 prosle
    assert ws.is_complete(s) is False    # mreza otkazana -> nije complete


def test_run_resume_from_stage2_runs_pages_3_to_6(tmp_path, monkeypatch):
    """Resume od stage 2 pokreće stranice 3-6 (model, mreža, mape, sažetak) — ne ponavlja 1-2."""
    from atlas.core.spine import init_spine
    from atlas.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    ws.set_stage(s, 2)
    ran = []
    monkeypatch.setattr(wizard, "page_preduvjeti", lambda *a, **k: ran.append("p1") or True)
    monkeypatch.setattr(wizard, "page_operater", lambda *a, **k: ran.append("p2") or True)
    monkeypatch.setattr(wizard, "page_model", lambda *a, **k: ran.append("p3") or True)
    monkeypatch.setattr(wizard, "page_mreza", lambda *a, **k: ran.append("p4") or True)
    monkeypatch.setattr(wizard, "page_mape", lambda *a, **k: ran.append("p5") or True)
    monkeypatch.setattr(wizard, "page_gotovo", lambda *a, **k: ran.append("p6") or True)
    monkeypatch.setattr(wizard, "launch_now", lambda *a, **k: None)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert ran == ["p3", "p4", "p5", "p6"]
    assert ws.is_complete(s) is True


def test_launch_now_odbijen_ne_pokrece_nista(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    calls = []
    lines = []
    wizard.launch_now(s, None, input_fn=_reader("n"), out=lines.append,
                      popen=lambda *a, **k: calls.append(a))
    assert calls == []
    assert any("atlas serve" in l for l in lines)


def test_launch_now_windows_pokrece_serve_i_edge(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    s.set_override("net", "host", "192.168.1.7")
    s.set_override("net", "port", "8443")
    monkeypatch.setattr(wizard.platform, "system", lambda: "Windows")
    calls = []
    wizard.launch_now(s, None, input_fn=_reader(""), out=lambda *_: None,
                      popen=lambda cmd, **k: calls.append(cmd))
    assert len(calls) == 2
    assert calls[0][-1] == "serve"
    assert calls[1][:4] == ["cmd", "/c", "start", ""]
    assert "--app=https://192.168.1.7:8443" in calls[1]


def test_launch_now_bind_sve_mreze_koristi_lan_ip(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    s.set_override("net", "host", "0.0.0.0")
    s.set_override("net", "port", "8443")
    monkeypatch.setattr(wizard.preflight, "local_ip", lambda: "192.168.1.7")
    lines = []
    wizard.launch_now(s, None, input_fn=_reader("n"), out=lines.append,
                      popen=lambda *a, **k: None)
    assert any("https://192.168.1.7:8443" in l for l in lines)


def test_launch_now_bez_mapa_env_none(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    calls = []
    wizard.launch_now(s, None, input_fn=_reader(""), out=lambda *_: None,
                      popen=lambda cmd, **k: calls.append(k))
    assert calls[0]["env"] is None


def test_launch_now_s_registriranom_mapom_prosljedjuje_mount_roots(tmp_path):
    import types
    from atlas.business import folders
    s = init_spine(str(tmp_path / "t.db"))
    mapa = tmp_path / "propisi"
    mapa.mkdir()
    folders.register(s, types.SimpleNamespace(mount_roots=[str(mapa)]),
                     str(mapa), "propisi", label="Propisi", user="test")
    calls = []
    wizard.launch_now(s, None, input_fn=_reader(""), out=lambda *_: None,
                      popen=lambda cmd, **k: calls.append(k))
    env = calls[0]["env"]
    assert env is not None
    assert str(mapa.resolve()) in env["ATLAS_MOUNT_ROOTS"].split(",")


def test_launch_now_oserror_ne_rusi(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))

    def _boom(cmd, **k):
        raise OSError("nema binarke")
    lines = []
    wizard.launch_now(s, None, input_fn=_reader(""), out=lines.append, popen=_boom)
    assert any("atlas serve" in l for l in lines)


def test_launch_now_eof_tretira_kao_ne(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))

    def _eof(_=""):
        raise EOFError()
    calls = []
    wizard.launch_now(s, None, input_fn=_eof, out=lambda *_: None,
                      popen=lambda *a, **k: calls.append(a))   # ne smije propagirati
    assert calls == []


def test_choose_embed_model_bge_when_ram_allows():
    st = {"ram_total_gb": 16.0}
    assert wizard.choose_embed_model(st, "mali-default") == "BAAI/bge-m3"


def test_choose_embed_model_fallback_on_small_ram():
    st = {"ram_total_gb": 2.0}   # 1.2/2.0 = 60% -> tight, ne "fits"
    assert wizard.choose_embed_model(st, "mali-default") == "mali-default"


def test_setup_embedding_falls_back_on_download_error(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    from atlas.config import Config
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
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


class _FakeRes:
    def __init__(self, text):
        self.text = text
        self.model = "test-model"


def test_self_test_ok_on_nonempty_answer(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "_llm_complete", lambda spine, cfg, prompt: _FakeRes("OK ATLAS"))
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


def _llmfit_rows():
    return [
        {"name": "hf/q7", "ollama_name": "qwen2.5:7b", "category": "Chat",
         "fit_label": "Good", "best_quant": "Q4_K_M", "memory_gb": 4.7,
         "tps": 11.0, "score": 90.0, "use_case": "opći asistent", "params": "7B"},
        {"name": "hf/l3", "ollama_name": "llama3.2:3b", "category": "Chat",
         "fit_label": "Marginal", "best_quant": "Q4_K_M", "memory_gb": 2.0,
         "tps": 20.0, "score": 70.0, "use_case": "brzi sažeci", "params": "3B"},
    ]


def test_page_model_skip_branch_when_ollama_unavailable(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (False, "nema"))
    monkeypatch.setattr(wizard.preflight, "start_ollama", lambda **k: False)
    # "da" na "preskoci i postavi kasnije?"
    ok = wizard.page_model(s, None, input_fn=_reader("da"), out=lambda *_: None)
    assert ok is True


def test_page_model_skip_when_llmfit_unavailable(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (True, "radi"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url=None: "0.6.0")
    monkeypatch.setattr(wizard.preflight, "llmfit_models", lambda c=None: None)
    # llmfit "instaliran" (na PATH-u) -> ne pita za auto-install, ide ravno na skip
    monkeypatch.setattr(wizard.shutil, "which", lambda name: "/usr/bin/llmfit")

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "def-emb"
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader(), out=lambda *_: None)
    assert ok is True    # llmfit nedostupan -> postavi kasnije, ne zaglavi


def test_page_model_llmfit_missing_declines_install(tmp_path, monkeypatch):
    """Nalaz d: llmfit binary nema na PATH-u -> ponudi pip auto-install; 'ne' ->
    install_llmfit se ne zove."""
    from atlas.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (True, "radi"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url=None: "0.6.0")
    monkeypatch.setattr(wizard.preflight, "llmfit_models", lambda c=None: None)
    monkeypatch.setattr(wizard.shutil, "which", lambda name: None)
    calls = []
    monkeypatch.setattr(wizard.preflight, "install_llmfit",
                        lambda out=print: calls.append(1) or True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "def-emb"
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader("ne"), out=lambda *_: None)
    assert ok is True
    assert calls == []


def test_page_model_llmfit_missing_accepts_install(tmp_path, monkeypatch):
    """'da' -> install_llmfit se zove (pip, bez os.name provjere)."""
    from atlas.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (True, "radi"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url=None: "0.6.0")
    monkeypatch.setattr(wizard.preflight, "llmfit_models", lambda c=None: None)
    monkeypatch.setattr(wizard.shutil, "which", lambda name: None)
    calls = []
    monkeypatch.setattr(wizard.preflight, "install_llmfit",
                        lambda out=print: calls.append(1) or True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "def-emb"
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader("d"), out=lambda *_: None)
    assert ok is True
    assert calls == [1]


def test_page_model_full_happy_path(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    from atlas.business import model_settings
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (True, "radi"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url=None: "0.6.0")
    monkeypatch.setattr(wizard.preflight, "llmfit_models",
                        lambda c=None: _llmfit_rows())
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
    assert pulled == ["qwen2.5:7b"]
    saved = model_settings.get(s)
    assert saved["provider"] == "ollama"
    assert saved["model"] == pulled[0]
    assert saved["embed_model"] == "emb-model"


def test_page_model_tablica_i_kontekst(tmp_path, monkeypatch):
    """Stranica 3 ispisuje slobodni RAM/disk i tablicu s Disk stupcem;
    odabir prvog reda pulla točan model."""
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url: (True, "ok"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url: "0.5.0")
    monkeypatch.setattr(wizard.preflight, "ollama_floor_ok", lambda v: True)
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ram_total_gb": 8.0, "ram_free_gb": 5.5,
                                        "disk_free_gb": 90.0})
    rows = [{"ollama_name": "qwen2.5:7b", "params": "7B", "best_quant": "Q4_K_M",
             "memory_gb": 5.2, "tps": 11.0, "fit_label": "Good", "use_case": ""}]
    monkeypatch.setattr(wizard.preflight, "llmfit_models", lambda cfg: rows)
    pulled = []
    monkeypatch.setattr(wizard.preflight, "ollama_pull",
                        lambda m, url, out=print: pulled.append(m) or True)
    monkeypatch.setattr(wizard, "setup_embedding", lambda s_, c, out=print: "emb")
    monkeypatch.setattr(wizard, "self_test",
                        lambda s_, c, input_fn=input, out=print: True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "x"
    lines = []
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader("1"), out=lines.append)
    assert ok is True and pulled == ["qwen2.5:7b"]
    text = "\n".join(lines)
    assert "Disk" in text            # zaglavlje tablice
    assert "90" in text and "5.5" in text   # kontekst stroja
    assert "RAM, ne disk" in text    # legenda


def test_render_summary_pokazuje_konfigurirano_i_preskoceno(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    s.set_override("net", "host", "192.168.1.7")
    s.set_override("net", "port", "8443")
    s.set_override("model", "model", "qwen2.5:7b")

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)
    lines = []
    wizard.render_summary(s, _Cfg(), out=lines.append)
    text = "\n".join(lines)
    assert "qwen2.5:7b" in text
    assert "https://192.168.1.7:8443" in text
    assert "Mape" in text            # preskočeno → uputa na Postavke
    assert "preskočeno" in text


def test_page_gotovo_backup_da(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)
    made = []
    monkeypatch.setattr(wizard.backup, "create_backup",
                        lambda cfg: made.append(1) or
                        {"name": "x", "path": str(tmp_path / "x.db"), "size": 7})
    lines = []
    ok = wizard.page_gotovo(s, _Cfg(), input_fn=_reader(""), out=lines.append)
    assert ok is True and made == [1]
    text = "\n".join(lines)
    assert ".ollama" in text and "secret" in text


def test_page_gotovo_backup_greska_ne_rusi(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)

    def _boom(cfg):
        raise RuntimeError("verifikacija pala")
    monkeypatch.setattr(wizard.backup, "create_backup", _boom)
    lines = []
    ok = wizard.page_gotovo(s, _Cfg(), input_fn=_reader("d"), out=lines.append)
    assert ok is True
    assert any("atlas backup" in l for l in lines)


def test_page_gotovo_ollama_putanja_windows(tmp_path, monkeypatch):
    """page_gotovo prikazuje putanju za Ollama modele — na Windowsu
    %USERPROFILE%\\.ollama\\models."""
    import types
    s = init_spine(str(tmp_path / "t.db"))

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)

    # Fake os SAMO u wizard namespaceu — globalni os.name patch na pravom
    # Windowsu tjera pathlib na PosixPath i ruši pytest repr (INTERNALERROR).
    monkeypatch.setattr(wizard, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(wizard.backup, "create_backup",
                        lambda cfg: {"name": "x", "path": str(tmp_path / "x.db"), "size": 7})
    lines = []
    ok = wizard.page_gotovo(s, _Cfg(), input_fn=_reader("n"), out=lines.append)
    assert ok is True
    text = "\n".join(lines)
    assert r"%USERPROFILE%\.ollama\models" in text


def test_page_gotovo_ollama_putanja_posix(tmp_path, monkeypatch):
    """page_gotovo prikazuje putanju za Ollama modele — na POSIX-u
    ~/.ollama/models."""
    import types
    s = init_spine(str(tmp_path / "t.db"))

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)

    monkeypatch.setattr(wizard, "os", types.SimpleNamespace(name="posix"))
    monkeypatch.setattr(wizard.backup, "create_backup",
                        lambda cfg: {"name": "x", "path": str(tmp_path / "x.db"), "size": 7})
    lines = []
    ok = wizard.page_gotovo(s, _Cfg(), input_fn=_reader("n"), out=lines.append)
    assert ok is True
    text = "\n".join(lines)
    assert "~/.ollama/models" in text


def test_launch_now_edge_oserror_ne_rusi(tmp_path, monkeypatch):
    """launch_now — OSError na drugom popenu (Edge) ne smije propagirati.
    Očekuj da se prvi popen (serve) uspješno pokrene, drugi (Edge) padne,
    izlaz sadrži 'Server pokrenut' i 'Otvori ručno' s URL-om."""
    s = init_spine(str(tmp_path / "t.db"))
    s.set_override("net", "host", "192.168.1.7")
    s.set_override("net", "port", "8443")
    monkeypatch.setattr(wizard.platform, "system", lambda: "Windows")

    calls = []

    def _popen_with_edge_fail(cmd, **k):
        calls.append(cmd)
        # Prvi poziv (serve) uspješan, drugi (Edge) pada s OSError
        if len(calls) == 2:
            raise OSError("msedge nije dostupna")

    lines = []
    wizard.launch_now(s, None, input_fn=_reader(""), out=lines.append,
                      popen=_popen_with_edge_fail)

    assert len(calls) == 2
    assert calls[0][-1] == "serve"
    assert calls[1][:4] == ["cmd", "/c", "start", ""]
    text = "\n".join(lines)
    assert "Server pokrenut" in text
    assert "Otvori ručno u pregledniku" in text
    assert "192.168.1.7:8443" in text


def _legacy_spine(tmp_path):
    """Stanje nakon --reset na već dovršenom sustavu: stage 0, bez complete
    flaga, admin + model + mreža postoje. (Prava predwizard baza se auto-
    dovrši migracijom kod otvaranja — spine._migrate_setup_complete_for_upgrades
    — prije nego run() uopće krene; ovaj fixture modelira jedini realni okidač.)"""
    s = init_spine(str(tmp_path / "t.db"))
    firstrun.create_first_owner(s, "admin", "lozinka123")
    s.set_override("model", "model", "qwen2.5:7b")
    s.set_override("net", "host", "192.168.1.7")
    return s


class _UpgCfg:
    db_path = ""
    data_dir = ""


def test_page_upgrade_da_vraca_true(tmp_path):
    s = _legacy_spine(tmp_path)
    lines = []
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader("d"), out=lines.append)
    assert ok is True
    text = "\n".join(lines)
    assert "Postojeća baza" in text
    assert "qwen2.5:7b" in text          # render_summary je prikazan


def test_page_upgrade_ne_vraca_false(tmp_path):
    s = _legacy_spine(tmp_path)
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader("n"), out=lambda *_: None)
    assert ok is False


def test_page_upgrade_default_da_kad_je_sve_postavljeno(tmp_path):
    s = _legacy_spine(tmp_path)
    # prazan unos = default; admin+model+host postavljeni → default da
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader(""), out=lambda *_: None)
    assert ok is True


def test_page_upgrade_default_ne_kad_fali_model(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    firstrun.create_first_owner(s, "admin", "lozinka123")   # samo admin, bez modela/mreže
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader(""), out=lambda *_: None)
    assert ok is False


def _stub_pages(monkeypatch, ran):
    for p in ("page_preduvjeti", "page_operater", "page_model",
              "page_mreza", "page_mape", "page_gotovo"):
        monkeypatch.setattr(wizard, p,
                            lambda *a, _p=p, **k: ran.append(_p) or True)
    monkeypatch.setattr(wizard, "launch_now", lambda *a, **k: None)


def test_run_legacy_baza_da_preskace_stranice(tmp_path, monkeypatch):
    s = _legacy_spine(tmp_path)
    ran = []
    _stub_pages(monkeypatch, ran)
    wizard.run(s, _UpgCfg(), input_fn=_reader("d"), out=lambda *_: None)
    assert ran == []
    assert ws.is_complete(s) is True


def test_run_legacy_baza_ne_ide_normalni_wizard(tmp_path, monkeypatch):
    s = _legacy_spine(tmp_path)
    ran = []
    _stub_pages(monkeypatch, ran)
    wizard.run(s, _UpgCfg(), input_fn=_reader("n"), out=lambda *_: None)
    assert "page_preduvjeti" in ran and "page_gotovo" in ran
    assert ws.is_complete(s) is True


def test_run_svjeza_baza_bez_ponude(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))          # bez korisnika
    ran = []
    _stub_pages(monkeypatch, ran)
    ponuda = []
    monkeypatch.setattr(wizard, "page_upgrade",
                        lambda *a, **k: ponuda.append(1) or True)
    wizard.run(s, _UpgCfg(), input_fn=_reader(), out=lambda *_: None)
    assert ponuda == []
    assert "page_preduvjeti" in ran


def test_run_resume_bez_ponude(tmp_path, monkeypatch):
    s = _legacy_spine(tmp_path)
    ws.set_stage(s, 1)                               # resume, ne upgrade
    ran = []
    _stub_pages(monkeypatch, ran)
    ponuda = []
    monkeypatch.setattr(wizard, "page_upgrade",
                        lambda *a, **k: ponuda.append(1) or True)
    wizard.run(s, _UpgCfg(), input_fn=_reader(), out=lambda *_: None)
    assert ponuda == []
    assert "page_operater" in ran and "page_preduvjeti" not in ran
