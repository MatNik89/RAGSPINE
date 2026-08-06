import pytest

from ragspine.ops import preflight as pf, wizard_state as ws


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    # requirements() bi inače stvarno zvao urlopen(:11434) i spajao se na
    # 8.8.8.8:53 — sporo/nepouzdano na CI bez mreže (i firewalled okolinama).
    # Testovi koji trebaju specifičnu vrijednost preklapaju ovo svojim
    # monkeypatch.setattr (izvršava se poslije, pa pobjeđuje).
    monkeypatch.setattr(pf, "ollama_ready", lambda url=None: (True, "servis radi"))
    monkeypatch.setattr(pf, "internet_ok", lambda *a, **k: True)


def test_internet_is_warn_not_fail(monkeypatch):
    monkeypatch.setattr(pf, "internet_ok", lambda: False)
    reqs = {r["key"]: r for r in pf.requirements()}
    assert reqs["internet"]["status"] == "warn"   # offline ne blokira


def test_system_state_has_ip_mode():
    st = pf.system_state()
    assert st["ip_mode"] in ("static", "dhcp", "unknown")


def test_tesseract_missing_is_fail(monkeypatch):
    monkeypatch.setattr(pf.shutil, "which", lambda _: None)
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
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "pw")           # prvi login → owner default orga
    ws.mark_complete(spine)  # gatekeeper drži na /ui/setup dok wizard ne završi
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_preflight_route_admin_only(spine, cfg):
    from fastapi.testclient import TestClient
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user
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
    monkeypatch.setattr(pf, "run_isolated",
                        lambda cmd, timeout=60: (0, _llmfit_json(models), ""))
    rows = pf.llmfit_models()
    assert rows is not None
    assert len(rows) == 12                              # cap
    assert rows[0]["ollama_name"] == "e:7b"             # najveci score prvi
    assert rows[1]["ollama_name"] == "a:7b"
    names = {r["ollama_name"] for r in rows}
    assert None not in names and "c:3b" not in names and "d:14b" not in names
    assert rows[0]["memory_gb"] == 4.7 and rows[0]["tps"] == 12.0


def test_llmfit_models_none_when_binary_fails(monkeypatch):
    monkeypatch.setattr(pf, "run_isolated", lambda cmd, timeout=60: (1, "", "boom"))
    assert pf.llmfit_models() is None


def test_llmfit_models_none_on_garbage(monkeypatch):
    monkeypatch.setattr(pf, "run_isolated", lambda cmd, timeout=60: (0, "nije json", ""))
    assert pf.llmfit_models() is None
