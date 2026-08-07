from pathlib import Path
from types import SimpleNamespace

from atlas.__main__ import main
from atlas.ops import doctor

def test_doctor_exit_0_despite_ollama_down(tmp_path, monkeypatch, capsys):
    # Ollama unreachable is expected on a non-Ollama (cloud-LLM/OAuth) host and
    # must not hard-fail `atlas doctor`'s exit code.
    # Disk space is stubbed healthy so this doesn't flake on a low-disk dev machine
    # (it tests ollama-vs-exit-code logic, not the real disk).
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "_ollama_alive", lambda cfg: False)
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(total=0, used=0, free=50_000_000_000))
    assert main(["doctor"]) == 0

def test_auth_add_and_doctor(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PASS", "tajna123")
    assert main(["auth", "add", "ana"]) == 0
    out = capsys.readouterr().out
    assert "ana" in out

def test_unknown_cmd():
    assert main(["nepostojece"]) == 2

def test_watch_ocr_wired(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    assert main(["watch", "run"]) == 0
    assert "changes=" in capsys.readouterr().out
    assert main(["ocr", "/nonexistent.pdf"]) == 1


def test_forget_command_removed(capsys):
    # brisanje klijentskih podataka namjerno NIJE dostupno (zakonska retencija)
    assert main(["forget", "bilo-sto"]) != 0


def test_net_overrides_apply(tmp_path, monkeypatch):
    from atlas.core.spine import init_spine
    from atlas.__main__ import _net_overrides
    from atlas.config import Config
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    s = init_spine(str(tmp_path / "t.db"))
    cfg = Config.from_env()
    host, port, cert, key = _net_overrides(s, cfg)
    assert (host, port) == (cfg.host, cfg.port)   # bez overridea -> cfg
    s.set_override("net", "host", "0.0.0.0")
    s.set_override("net", "port", "8443")
    s.set_override("net", "cert_path", "/x/cert.pem")
    s.set_override("net", "key_path", "/x/key.pem")
    host, port, cert, key = _net_overrides(s, cfg)
    assert (host, port, cert, key) == ("0.0.0.0", 8443, "/x/cert.pem", "/x/key.pem")


def test_serve_warns_when_cert_key_missing(cfg, monkeypatch, capsys):
    """Nalaz c: cert/key overridi postavljeni ali datoteke ne postoje -> mora
    upozoriti prije tihog pada na HTTP (a ne samo šutke nastaviti)."""
    import uvicorn
    from atlas.core.spine import init_spine
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    s = init_spine(cfg.db_path)
    s.set_override("net", "cert_path", str(Path(cfg.data_dir) / "nope.pem"))
    s.set_override("net", "key_path", str(Path(cfg.data_dir) / "nope-key.pem"))
    assert main(["serve"]) == 0
    assert "bez HTTPS" in capsys.readouterr().out


def test_serve_starts_bootstrap_when_cert_present(cfg, monkeypatch, capsys):
    """Kad cert/key postoje (HTTPS aktivan), serve mora pokrenuti bootstrap
    HTTP server za radnike i ispisati uputu s /postavi adresom."""
    import uvicorn
    from atlas.core.spine import init_spine
    from atlas.web import bootstrap_http

    cert_p = Path(cfg.data_dir) / "cert.pem"
    key_p = Path(cfg.data_dir) / "key.pem"
    cert_p.write_text("cert"); key_p.write_text("key")

    calls = {}

    def fake_start(cert, https_url, host, port=8080):
        calls["args"] = (cert, https_url, host, port)
        return object()  # ne-None => "uspio"

    monkeypatch.setattr(bootstrap_http, "start_bootstrap_server", fake_start)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    s = init_spine(cfg.db_path)
    s.set_override("net", "cert_path", str(cert_p))
    s.set_override("net", "key_path", str(key_p))

    assert main(["serve"]) == 0
    assert calls["args"][0] == str(cert_p)
    assert "https://" in calls["args"][1]
    out = capsys.readouterr().out
    assert "Bootstrap za radnike:" in out
    assert "/postavi" in out


def test_serve_bootstrap_port_zero_skips(cfg, monkeypatch, capsys):
    """ATLAS_BOOTSTRAP_PORT=0 isključuje bootstrap server."""
    import uvicorn
    from atlas.core.spine import init_spine
    from atlas.web import bootstrap_http

    cert_p = Path(cfg.data_dir) / "cert.pem"
    key_p = Path(cfg.data_dir) / "key.pem"
    cert_p.write_text("cert"); key_p.write_text("key")

    called = []
    monkeypatch.setattr(bootstrap_http, "start_bootstrap_server",
                        lambda *a, **k: called.append(1))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setenv("ATLAS_BOOTSTRAP_PORT", "0")
    s = init_spine(cfg.db_path)
    s.set_override("net", "cert_path", str(cert_p))
    s.set_override("net", "key_path", str(key_p))

    assert main(["serve"]) == 0
    assert not called
    assert "Bootstrap za radnike:" not in capsys.readouterr().out
