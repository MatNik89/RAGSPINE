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


def test_servis_status_ne_treba_elevaciju(tmp_path, monkeypatch, capsys):
    from atlas.ops import winsvc
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(winsvc, "service_status", lambda **k: "not-installed")
    assert main(["servis", "status"]) == 0
    assert "not-installed" in capsys.readouterr().out


def test_servis_install_bez_elevacije_vraca_1(tmp_path, monkeypatch, capsys):
    import atlas.__main__ as m
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_elevated", lambda: False)
    assert main(["servis", "install"]) == 1
    out = capsys.readouterr().out.lower()
    assert "administrator" in out or "sudo" in out


def test_servis_uninstall_bez_elevacije_vraca_1(tmp_path, monkeypatch):
    import atlas.__main__ as m
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_elevated", lambda: False)
    assert main(["servis", "uninstall"]) == 1


def test_servis_install_elevated_zove_winsvc(cfg, monkeypatch):
    # cfg fixture (ne samo ATLAS_DATA_DIR env): _cmd_servis ide preko
    # get_config(), koji keša globalno — bez cfg fixture teardowna curi u
    # susjedne testove (get_config() vrati stari data_dir iz ranijeg testa).
    import atlas.__main__ as m
    from atlas.ops import winsvc
    monkeypatch.setattr(m, "_elevated", lambda: True)
    calls = []
    monkeypatch.setattr(winsvc, "install_service",
                        lambda exe, data_dir, port, **k: calls.append((exe, data_dir, port)) or True)
    assert main(["servis", "install"]) == 0
    assert calls and calls[0][1] == cfg.data_dir


def test_servis_install_elevated_pad_vraca_1(cfg, monkeypatch):
    import atlas.__main__ as m
    from atlas.ops import winsvc
    monkeypatch.setattr(m, "_elevated", lambda: True)
    monkeypatch.setattr(winsvc, "install_service", lambda *a, **k: False)
    assert main(["servis", "install"]) == 1


def test_servis_uninstall_elevated_zove_winsvc(cfg, monkeypatch):
    import atlas.__main__ as m
    from atlas.ops import winsvc
    monkeypatch.setattr(m, "_elevated", lambda: True)
    calls = []
    monkeypatch.setattr(winsvc, "uninstall_service",
                        lambda data_dir, **k: calls.append(data_dir) or True)
    assert main(["servis", "uninstall"]) == 0
    assert calls == [cfg.data_dir]


def test_servis_nepoznata_akcija_exit_2():
    assert main(["servis", "sta-god"]) == 2


def test_elevated_windows_admin(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    import atlas.__main__ as m
    monkeypatch.setattr(m.os, "name", "nt")
    monkeypatch.setattr(ctypes, "windll",
                        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
                        raising=False)
    assert m._elevated() is True


def test_elevated_windows_bez_admin_api_vraca_false(monkeypatch):
    import ctypes
    import atlas.__main__ as m
    monkeypatch.setattr(m.os, "name", "nt")
    monkeypatch.delattr(ctypes, "windll", raising=False)   # simulira ne-Windows ctypes
    assert m._elevated() is False


def test_elevated_posix_root(monkeypatch):
    import atlas.__main__ as m
    monkeypatch.setattr(m.os, "name", "posix")
    monkeypatch.setattr(m.os, "geteuid", lambda: 0, raising=False)
    assert m._elevated() is True


def test_elevated_posix_non_root(monkeypatch):
    import atlas.__main__ as m
    monkeypatch.setattr(m.os, "name", "posix")
    monkeypatch.setattr(m.os, "geteuid", lambda: 1000, raising=False)
    assert m._elevated() is False


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
