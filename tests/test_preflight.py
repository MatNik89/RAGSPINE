import os

import pytest

from atlas.ops import preflight as pf
from tests.conftest import complete_setup

# Sačuvana referenca na pravu funkciju PRIJE nego je autouse fixture prekrije
# (Nalaz #3, P2b review) — dedicirani llmfit_models/summary testovi je zovu
# izravno da izbjegnu stvarni subprocess poziv, a ostali testovi ne trebaju znati za nju.
_real_llmfit_models = pf.llmfit_models


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    # requirements() bi inače stvarno zvao urlopen(:11434) i spajao se na
    # 8.8.8.8:53 — sporo/nepouzdano na CI bez mreže (i firewalled okolinama).
    # Testovi koji trebaju specifičnu vrijednost preklapaju ovo svojim
    # monkeypatch.setattr (izvršava se poslije, pa pobjeđuje).
    monkeypatch.setattr(pf, "ollama_ready", lambda url=None: (True, "servis radi"))
    monkeypatch.setattr(pf, "internet_ok", lambda *a, **k: True)
    # llmfit_models() inače šalje pravi subprocess (8 MB JSON, ovisan o stroju) —
    # zamijeni ga praznom listom; dedicirani testovi ispod vraćaju pravu funkciju
    # preko _real_llmfit_models (Nalaz #3, P2b review).
    monkeypatch.setattr(pf, "llmfit_models", lambda cfg=None: [])


def test_internet_is_warn_not_fail(monkeypatch):
    monkeypatch.setattr(pf, "internet_ok", lambda: False)
    reqs = {r["key"]: r for r in pf.requirements()}
    assert reqs["internet"]["status"] == "warn"   # offline ne blokira


def test_system_state_has_ip_mode():
    st = pf.system_state()
    assert st["ip_mode"] in ("static", "dhcp", "unknown")


def test_tesseract_missing_is_fail(monkeypatch):
    # winpath.find_binary radi vlastiti shutil.which unutar svog modula —
    # patchati treba njega, ne pf.shutil (inače stvarni tesseract na
    # razvojnom stroju "pobjeđuje" monkeypatch — Nalaz zadatka 3).
    monkeypatch.setattr(pf.winpath, "find_binary", lambda k: None)
    reqs = {r["key"]: r for r in pf.requirements()}
    assert reqs["tesseract"]["status"] == "fail"   # bio "warn"


def test_ollama_row_present(monkeypatch):
    monkeypatch.setattr(pf, "ollama_ready", lambda url=None: (False, "nije dostupna"))
    reqs = {r["key"]: r for r in pf.requirements()}
    assert "ollama" in reqs
    assert reqs["ollama"]["status"] in ("warn", "fail")
    assert "Ollama" in reqs["ollama"]["naziv"]


def test_fit_pill_fractions_of_total():
    # udio ukupnog RAM-a: <50% stane, 50-70% tijesno, >=70% ne
    assert pf.fit_pill(4.0, 10) == "fits"     # 40%
    assert pf.fit_pill(6.0, 10) == "tight"    # 60%
    assert pf.fit_pill(7.5, 10) == "too_big"  # 75%
    assert pf.fit_pill(1.0, 0) == "unknown"


def test_requirements_python_and_structure(cfg):
    reqs = pf.requirements(cfg)
    py = next(r for r in reqs if r["key"] == "python")
    assert py["status"] == "ok"
    dd = next(r for r in reqs if r["key"] == "data_dir")
    assert dd["status"] == "ok"  # cfg data_dir je upisiv (write-probe)
    for r in reqs:
        assert set(r) >= {"key", "naziv", "status", "detalj", "fix"}
        assert r["status"] in ("ok", "warn", "fail")


def test_system_state_keys(cfg):
    st = pf.system_state(cfg)
    assert set(st) >= {"os", "python", "ram_total_gb", "ram_free_gb", "disk_free_gb", "vram_gb"}
    assert st["ram_total_gb"] >= 0


def _admin_client(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "pw")           # prvi login → owner default orga
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_preflight_route_admin_only(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    # owner (admin) → 200
    c, h = _admin_client(spine, cfg)
    j = c.get("/preflight", headers=h)
    assert j.status_code == 200
    assert {"state", "requirements", "models"} <= set(j.json())
    assert c.get("/ui/racunalo", headers=h).status_code == 200
    # običan radnik → 403
    add_user(spine, "boris", "pw", "radnik")
    wtok = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    assert c.get("/preflight", headers={"Authorization": f"Bearer {wtok}"}).status_code == 403


def test_ollama_version_parses(monkeypatch):
    import io, json

    class _Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(pf.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(json.dumps({"version": "0.5.4"}).encode()))
    assert pf.ollama_version("http://x") == "0.5.4"


def test_ollama_version_none_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("nema servisa")
    monkeypatch.setattr(pf.urllib.request, "urlopen", _boom)
    assert pf.ollama_version("http://x") is None


def test_ollama_floor_ok():
    assert pf.ollama_floor_ok("0.5.0") is True
    assert pf.ollama_floor_ok("0.12.1") is True
    assert pf.ollama_floor_ok("0.4.9") is False
    assert pf.ollama_floor_ok(None) is False
    assert pf.ollama_floor_ok("čudno") is False


def test_start_ollama_returns_true_when_service_comes_up(monkeypatch):
    monkeypatch.setattr(pf.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(pf, "ollama_ready", lambda url=None: (True, "servis radi"))
    assert pf.start_ollama(wait_s=0.1) is True


def test_start_ollama_false_when_binary_missing(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("ollama")
    monkeypatch.setattr(pf.subprocess, "Popen", _boom)
    assert pf.start_ollama(wait_s=0.1) is False


def test_start_ollama_windows_uses_detached_flags(monkeypatch):
    captured = {}

    def _popen(cmd, **kw):
        captured.update(kw)
        return object()
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pf.subprocess, "Popen", _popen)
    monkeypatch.setattr(pf, "ollama_ready", lambda url=None: (True, "radi"))
    assert pf.start_ollama(wait_s=0.1) is True
    assert captured.get("creationflags", 0) != 0
    assert "start_new_session" not in captured


def _ndjson_resp(lines):
    import io, json
    payload = b"".join(json.dumps(l).encode() + b"\n" for l in lines)

    class _Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _Resp(payload)


def test_ollama_pull_success_with_progress(monkeypatch):
    resp = _ndjson_resp([
        {"status": "pulling manifest"},
        {"status": "downloading", "total": 100, "completed": 50},
        {"status": "success"},
    ])
    monkeypatch.setattr(pf.urllib.request, "urlopen", lambda *a, **k: resp)
    lines = []
    assert pf.ollama_pull("qwen2.5:7b", "http://x", out=lines.append) is True
    assert any("50%" in l for l in lines)


def test_ollama_pull_false_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("mreza pukla")
    monkeypatch.setattr(pf.urllib.request, "urlopen", _boom)
    lines = []
    assert pf.ollama_pull("qwen2.5:7b", "http://x", out=lines.append) is False
    assert any("Gre" in l for l in lines)   # "Greska..." poruka, ne traceback


def _llmfit_json(models):
    import json
    return json.dumps({"models": models, "system": {}})


def _lm(name, ollama, cat="Chat", fit="Good", score=50.0):
    return {"name": name, "ollama_name": ollama, "category": cat,
            "fit_label": fit, "best_quant": "Q4_K_M", "memory_required_gb": 4.7,
            "estimated_tps": 12.0, "score": score, "use_case": "chat",
            "parameter_count": "7B"}


def test_llmfit_models_filters_sorts_caps(monkeypatch):
    models = [
        _lm("hf/a", "a:7b", score=60.0),
        _lm("hf/b", None),                             # bez ollama_name -> van
        _lm("hf/c", "c:3b", cat="Embedding"),          # kriva kategorija -> van
        _lm("hf/d", "d:14b", fit="Too Tight"),         # ne stane -> van
        _lm("hf/e", "e:7b", cat="Reasoning", score=90.0),
    ] + [_lm(f"hf/x{i}", f"x{i}:1b", score=float(i)) for i in range(15)]
    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, _llmfit_json(models), ""))
    rows = pf.llmfit_models()
    assert rows is not None
    assert len(rows) == 12                              # cap
    assert rows[0]["ollama_name"] == "e:7b"             # najveci score prvi
    assert rows[1]["ollama_name"] == "a:7b"
    names = {r["ollama_name"] for r in rows}
    assert None not in names and "c:3b" not in names and "d:14b" not in names
    assert rows[0]["memory_gb"] == 4.7 and rows[0]["tps"] == 12.0


def test_llmfit_models_none_when_binary_fails(monkeypatch):
    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated", lambda cmd, timeout=60, **kw: (1, "", "boom"))
    assert pf.llmfit_models() is None


def test_llmfit_models_none_on_garbage(monkeypatch):
    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated", lambda cmd, timeout=60, **kw: (0, "nije json", ""))
    assert pf.llmfit_models() is None


def test_llmfit_models_uses_which_when_available(monkeypatch):
    # When llmfit binary is on PATH, use it directly
    captured_cmd = []

    def capture_run_isolated(cmd, timeout=60, **kw):
        captured_cmd.append(cmd)
        return (0, _llmfit_json([_lm("hf/a", "a:7b")]), "")

    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated", capture_run_isolated)
    monkeypatch.setattr(pf.shutil, "which", lambda x: "/x/llmfit" if x == "llmfit" else None)
    monkeypatch.setattr(pf, "system_state", lambda cfg=None: {"ram_total_gb": 0})  # bez --ram šuma

    rows = pf.llmfit_models()
    assert rows is not None
    assert captured_cmd[0] == ["/x/llmfit", "--json"]


def test_llmfit_models_fallback_to_python_m_when_which_fails(monkeypatch):
    # When which("llmfit") returns None, fall back to python -m wrapper
    captured_cmd = []

    def capture_run_isolated(cmd, timeout=60, **kw):
        captured_cmd.append(cmd)
        return (0, _llmfit_json([_lm("hf/a", "a:7b")]), "")

    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated", capture_run_isolated)
    monkeypatch.setattr(pf.shutil, "which", lambda x: None)
    monkeypatch.setattr(pf, "system_state", lambda cfg=None: {"ram_total_gb": 0})  # bez --ram šuma

    rows = pf.llmfit_models()
    assert rows is not None
    assert captured_cmd[0] == [pf.sys.executable, "-m", "llmfit", "--json"]


def test_summary_models_have_llmfit_shape(monkeypatch, cfg):
    # Shape guard: summary()["models"] rows must contain llmfit keys (not old model_fits keys)
    models = [_lm("hf/a", "a:7b", score=60.0), _lm("hf/b", "b:3b", score=50.0)]
    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, _llmfit_json(models), ""))
    s = pf.summary(cfg)
    assert "models" in s
    assert len(s["models"]) > 0
    # Check that rows have llmfit shape (ollama_name, fit_label, tps, etc.)
    for m in s["models"]:
        assert "ollama_name" in m, "Row must have ollama_name (from llmfit)"
        assert "fit_label" in m, "Row must have fit_label (Good/Marginal/Too Tight)"
        assert "tps" in m, "Row must have estimated_tps"
        assert "memory_gb" in m, "Row must have memory_required_gb"
        assert "best_quant" in m, "Row must have best_quant"
        # Old model_fits keys should NOT exist
        assert "role" not in m, "Old model_fits key 'role' should not exist"
        assert "quants" not in m, "Old model_fits key 'quants' should not exist"
        assert "tight_quant" not in m, "Old model_fits key 'tight_quant' should not exist"


def test_llmfit_models_passes_ram_ukupno(monkeypatch):
    # Nalaz #1: sizing mora ići po UKUPNOM RAM-u, ne trenutno slobodnom —
    # inače na opterećenom stroju llmfit sve modele vidi kao "Too Tight".
    captured_cmd = []

    def capture_run_isolated(cmd, timeout=60, **kw):
        captured_cmd.append(cmd)
        return (0, _llmfit_json([_lm("hf/a", "a:7b")]), "")

    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated", capture_run_isolated)
    monkeypatch.setattr(pf, "system_state", lambda cfg=None: {"ram_total_gb": 16.0})

    rows = pf.llmfit_models()
    assert rows is not None
    assert ["--ram", "16G"] == captured_cmd[0][-2:]


def test_llmfit_models_no_ram_flag_when_total_unknown(monkeypatch):
    captured_cmd = []

    def capture_run_isolated(cmd, timeout=60, **kw):
        captured_cmd.append(cmd)
        return (0, _llmfit_json([_lm("hf/a", "a:7b")]), "")

    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated", capture_run_isolated)
    monkeypatch.setattr(pf, "system_state", lambda cfg=None: {"ram_total_gb": 0})

    rows = pf.llmfit_models()
    assert rows is not None
    assert "--ram" not in captured_cmd[0]


def test_llmfit_models_dedups_by_ollama_name(monkeypatch):
    # Nalaz #2: više HF varijanti zna mapirati na isti Ollama tag — zadrži samo
    # onu s najvišim score-om (lista je sortirana score desc, prvo pojavljivanje pobjeđuje).
    models = [
        _lm("hf/a-low", "a:7b", score=10.0),
        _lm("hf/a-high", "a:7b", score=90.0),
        _lm("hf/b", "b:3b", score=50.0),
    ]
    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "system_state", lambda cfg=None: {"ram_total_gb": 0})
    monkeypatch.setattr(pf, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, _llmfit_json(models), ""))
    rows = pf.llmfit_models()
    assert rows is not None
    names = [r["ollama_name"] for r in rows]
    assert names.count("a:7b") == 1
    assert len(rows) == len(set(names))            # cap broji distinktne modele
    a_row = next(r for r in rows if r["ollama_name"] == "a:7b")
    assert a_row["name"] == "hf/a-high"             # veći score pobjeđuje


def test_local_ip_returns_string(monkeypatch):
    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def connect(self, addr): pass
        def getsockname(self): return ("192.168.1.7", 12345)
    monkeypatch.setattr(pf.socket, "socket", lambda *a, **k: _S())
    assert pf.local_ip() == "192.168.1.7"


def test_local_ip_fallback_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("nema mreže")
    monkeypatch.setattr(pf.socket, "socket", _boom)
    assert pf.local_ip() == "127.0.0.1"


def test_port_free_true_and_false():
    import socket as s
    srv = s.socket(s.AF_INET, s.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    taken = srv.getsockname()[1]
    try:
        assert pf.port_free("127.0.0.1", taken) is False
        srv2 = s.socket(s.AF_INET, s.SOCK_STREAM)
        srv2.bind(("127.0.0.1", 0))
        free = srv2.getsockname()[1]
        srv2.close()
        assert pf.port_free("127.0.0.1", free) is True
    finally:
        srv.close()


def test_port_free_windows_skips_reuseaddr(monkeypatch):
    """Nalaz b: SO_REUSEADDR na Windowsu ima drugu semantiku (dopušta bind na
    zauzet port) — Windows granom se ono NE smije postaviti."""
    calls = []

    class _FakeSock:
        def setsockopt(self, level, optname, value):
            calls.append(optname)

        def bind(self, addr):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pf.os, "name", "nt")
    monkeypatch.setattr(pf.socket, "socket", lambda *a, **k: _FakeSock())
    assert pf.port_free("127.0.0.1", 12345) is True
    assert pf.socket.SO_REUSEADDR not in calls


def test_install_via_winget_vec_instalirano(monkeypatch):
    """Postojeća binarka → 'već instalirano', winget se NE zove."""
    from atlas.ops import winpath
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winpath, "find_binary", lambda k: r"C:\alat\ollama.exe")
    monkeypatch.setattr(pf, "run_streaming",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("winget pozvan")))
    lines = []
    assert pf.install_via_winget("ollama", out=lines.append) is True
    assert any("već instaliran" in l for l in lines)


def test_install_via_winget_streaming_i_najava(monkeypatch, tmp_path):
    from atlas.ops import winpath
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    exe = tmp_path / "ollama.exe"
    hits = iter([None, str(exe)])   # prije installa nema, poslije ima
    monkeypatch.setattr(winpath, "find_binary", lambda k: next(hits, str(exe)))
    monkeypatch.setattr(winpath, "refresh_path_from_registry", lambda: True)
    monkeypatch.setattr(winpath, "append_user_path", lambda d: True)
    monkeypatch.setattr(pf.shutil, "which", lambda k: None)
    calls = []
    monkeypatch.setattr(pf, "run_streaming",
                        lambda cmd, **k: calls.append(cmd) or 0)
    exe.write_bytes(b"x")
    lines = []
    assert pf.install_via_winget("ollama", out=lines.append) is True
    assert calls and "winget" in calls[0][0]
    assert any("700 MB" in l for l in lines)      # najava veličine (E2E nalaz)


def test_install_via_winget_tesseract_zove_traineddata(monkeypatch, tmp_path):
    from atlas.ops import winpath
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))
    monkeypatch.setattr(pf.shutil, "which", lambda k: str(exe))
    called = []
    monkeypatch.setattr(pf, "ensure_traineddata",
                        lambda langs=("hrv", "eng"), out=print, urlopen=None:
                        called.append(langs) or True)
    assert pf.install_via_winget("tesseract", out=lambda *_: None) is True
    assert called


def test_install_via_winget_binarka_ne_nadjena_nakon_installa(monkeypatch, tmp_path):
    """Instalacija 'uspije' (rc 0) ali binarka se ne nađe nigdje poznatom —
    javi grešku umjesto 'restartaj terminal'."""
    from atlas.ops import winpath
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winpath, "find_binary", lambda k: None)
    monkeypatch.setattr(pf, "run_streaming", lambda *a, **k: 0)
    monkeypatch.setattr(winpath, "refresh_path_from_registry", lambda: True)
    lines = []
    assert pf.install_via_winget("ollama", out=lines.append) is False
    assert any("nije pronađen" in l for l in lines)


def test_install_via_winget_non_windows_prints_cmd(monkeypatch):
    monkeypatch.setattr(pf.platform, "system", lambda: "Linux")
    called = []
    monkeypatch.setattr(pf, "run_isolated",
                        lambda *a, **k: called.append(1) or (0, "", ""))
    lines = []
    assert pf.install_via_winget("tesseract", out=lines.append) is False
    assert not called                       # ništa se ne izvršava
    assert any("winget install" in l for l in lines)


def test_install_via_winget_unknown_key():
    with pytest.raises(ValueError):
        pf.install_via_winget("nepoznato")


def test_proxy_roundtrip(tmp_path):
    from atlas.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    assert pf.get_proxy(s) == ""
    pf.set_proxy(s, "http://proxy.ured.local:3128")
    assert pf.get_proxy(s) == "http://proxy.ured.local:3128"
    pf.set_proxy(s, "")
    assert pf.get_proxy(s) == ""


def test_llmfit_models_kesira_po_procesu(monkeypatch):
    calls = []
    payload = '{"models": []}'

    def _fake_run(cmd, timeout=60, mem_mb=1024):
        calls.append(cmd)
        return 0, payload, ""
    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated", _fake_run)
    monkeypatch.setattr(pf, "system_state", lambda cfg=None: {"ram_total_gb": 16})
    first = pf.llmfit_models(None)
    second = pf.llmfit_models(None)
    assert first == [] and second == []
    assert len(calls) == 1          # drugi poziv ide iz keša


def test_llmfit_models_ne_kesira_neuspjeh(monkeypatch):
    rcs = iter([1, 0])
    monkeypatch.setattr(pf, "llmfit_models", _real_llmfit_models)
    monkeypatch.setattr(pf, "run_isolated",
                        lambda cmd, timeout=60, mem_mb=1024: (next(rcs), '{"models": []}', ""))
    monkeypatch.setattr(pf, "system_state", lambda cfg=None: {"ram_total_gb": 16})
    assert pf.llmfit_models(None) is None    # rc=1 → None, ne kešira se
    assert pf.llmfit_models(None) == []      # sljedeći pokušaj ponovno pokreće


def _resp(data: bytes):
    import io

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _R(data)


def test_ensure_traineddata_skida_u_tessdata_uz_exe(tmp_path, monkeypatch):
    from atlas.ops import winpath
    exe = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    (exe.parent / "tessdata").mkdir()
    (exe.parent / "tessdata" / "eng.traineddata").write_bytes(b"eng")
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))
    urls = []
    lines = []
    ok = pf.ensure_traineddata(
        ("hrv", "eng"), out=lines.append,
        urlopen=lambda url, timeout=120: urls.append(url) or _resp(b"HRVDATA"))
    assert ok is True
    assert (exe.parent / "tessdata" / "hrv.traineddata").read_bytes() == b"HRVDATA"
    assert len(urls) == 1 and "hrv.traineddata" in urls[0]   # eng već postoji


def test_ensure_traineddata_fallback_na_data_dir(tmp_path, monkeypatch):
    """Program Files bez dozvole → data_dir/tessdata + TESSDATA_PREFIX;
    eng se KOPIRA iz primarne lokacije (TESSDATA_PREFIX je isključiv)."""
    from atlas.ops import winpath
    from atlas import config
    exe = tmp_path / "pf" / "tesseract.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    td = exe.parent / "tessdata"
    td.mkdir()
    (td / "eng.traineddata").write_bytes(b"ENG")
    datadir = tmp_path / "data"
    datadir.mkdir()
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))
    monkeypatch.setattr(config, "default_data_dir", lambda: str(datadir))
    monkeypatch.setattr(pf, "_dir_writable", lambda d: d != td)
    persisted = []
    monkeypatch.setattr(winpath, "persist_user_env",
                        lambda n, v: persisted.append((n, v)) or True)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    ok = pf.ensure_traineddata(
        ("hrv", "eng"), out=lambda *_: None,
        urlopen=lambda url, timeout=120: _resp(b"HRV"))
    assert ok is True
    dest = datadir / "tessdata"
    assert (dest / "hrv.traineddata").read_bytes() == b"HRV"
    assert (dest / "eng.traineddata").read_bytes() == b"ENG"     # kopiran
    assert os.environ["TESSDATA_PREFIX"] == str(dest)
    assert ("TESSDATA_PREFIX", str(dest)) in persisted


def test_ensure_traineddata_bez_tesseracta(monkeypatch):
    from atlas.ops import winpath
    monkeypatch.setattr(winpath, "find_binary", lambda k: None)
    assert pf.ensure_traineddata(out=lambda *_: None, urlopen=None) is False


def test_ensure_traineddata_download_pada(tmp_path, monkeypatch):
    from atlas.ops import winpath
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"x")
    (tmp_path / "tessdata").mkdir()
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))

    def _boom(url, timeout=120):
        raise OSError("mreža pala")
    ok = pf.ensure_traineddata(("hrv",), out=lambda *_: None, urlopen=_boom)
    assert ok is False


def test_ollama_pull_injektirani_out_svakih_10_posto(monkeypatch):
    """Injektirani out: redak svakih 10 % (ne 100 redaka spama — E2E nalaz)."""
    import io, json
    events = [json.dumps({"status": "pulling", "total": 100, "completed": i}).encode() + b"\n"
              for i in range(1, 101)]
    events.append(json.dumps({"status": "success"}).encode() + b"\n")
    body = io.BytesIO(b"".join(events))
    body.__enter__ = lambda *a: body
    body.__exit__ = lambda *a: False
    monkeypatch.setattr(pf.urllib.request, "urlopen",
                        lambda req, timeout=30: body)
    lines = []
    assert pf.ollama_pull("m", out=lines.append) is True
    pct_lines = [l for l in lines if "%" in l]
    assert 9 <= len(pct_lines) <= 12   # ~svakih 10 %, ne 100 redaka


def test_quant_tags_kandidati():
    from atlas.ops import preflight
    assert preflight.quant_tags("qwen2.5:7b", "Q4_K_M") == [
        "qwen2.5:7b-instruct-q4_K_M", "qwen2.5:7b-q4_K_M"]


def test_quant_tags_prazno_bez_kvanta_ili_taga():
    from atlas.ops import preflight
    assert preflight.quant_tags("qwen2.5:7b", "") == []
    assert preflight.quant_tags("bezdvotocke", "Q4_K_M") == []


def test_ollama_model_size_iz_api_tags(monkeypatch):
    from atlas.ops import preflight
    import io, json
    body = json.dumps({"models": [
        {"name": "qwen2.5:7b-instruct-q4_K_M", "size": 4_700_000_000},
        {"name": "phi3:mini", "size": 2_200_000_000}]}).encode()

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(preflight.urllib.request, "urlopen",
                        lambda url, timeout=10: _R(body))
    assert abs(preflight.ollama_model_size("phi3:mini", "http://x") - 2.2) < 0.1
    assert preflight.ollama_model_size("nema:tag", "http://x") == 0.0


def test_ollama_model_size_greska_nula(monkeypatch):
    from atlas.ops import preflight
    def _boom(url, timeout=10):
        raise OSError("dolje")
    monkeypatch.setattr(preflight.urllib.request, "urlopen", _boom)
    assert preflight.ollama_model_size("x:y", "http://x") == 0.0


def test_requirements_ne_pusta_warnings(monkeypatch, recwarn):
    """Optional-modul importi (fitz deprecation i sl.) ne smiju curiti u
    izlaz stranice 1."""
    import warnings
    from atlas.ops import preflight

    def _noisy_import(name):
        warnings.warn("fitz is deprecated", DeprecationWarning)
        raise ImportError("nema")
    import importlib
    monkeypatch.setattr(importlib, "import_module", _noisy_import)
    monkeypatch.setattr(preflight, "system_state",
                        lambda c=None: {"python": "3.11", "ram_total_gb": 8,
                                        "ram_free_gb": 4, "disk_free_gb": 50})
    monkeypatch.setattr(preflight, "ollama_ready", lambda url: (True, "ok"))
    monkeypatch.setattr(preflight, "internet_ok", lambda: True)
    monkeypatch.setattr(preflight.winpath, "find_binary", lambda k: None)
    preflight.requirements(None)
    assert len(recwarn) == 0
