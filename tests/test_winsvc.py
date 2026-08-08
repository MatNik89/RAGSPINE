# tests/test_winsvc.py
from atlas.ops import winsvc


# --- resolve_atlas_cmd (exe/args RAZDVOJENI — review CRITICAL 1) ----------

def test_resolve_atlas_cmd_atlas_na_pathu(monkeypatch):
    monkeypatch.setattr(winsvc.shutil, "which", lambda name: "/usr/local/bin/atlas")
    exe, args = winsvc.resolve_atlas_cmd()
    assert exe == "/usr/local/bin/atlas"
    assert args == ["serve"]


def test_resolve_atlas_cmd_fallback_python_m(monkeypatch):
    monkeypatch.setattr(winsvc.shutil, "which", lambda name: None)
    exe, args = winsvc.resolve_atlas_cmd()
    assert exe == winsvc.sys.executable
    assert args == ["-m", "atlas", "serve"]


# --- service_commands (firewall + ACL, sc.exe zamijenjen WinSW-om) --------

def test_service_commands_shapes():
    cmds = winsvc.service_commands("C:/data", 8443)
    assert all(isinstance(c, list) for c in cmds)
    assert len(cmds) == 2
    flat = [" ".join(c) for c in cmds]
    assert any("advfirewall" in f and "8443" in f for f in flat)
    assert any("icacls" in f and "C:/data" in f and "LocalService" in f for f in flat)
    assert all("sc.exe" not in f for f in flat)


def test_systemd_unit_content():
    u = winsvc.systemd_unit("/usr/bin/atlas", ["serve"], "/var/rs")
    assert "[Service]" in u and "Restart=on-failure" in u
    assert "ExecStart=/usr/bin/atlas serve" in u


def test_systemd_unit_multi_arg_exe_word_splits():
    """Python fallback (exe, args razdvojeni) i dalje radi za systemd — spaja
    se u jedan ExecStart string koji systemd sam word-splita."""
    u = winsvc.systemd_unit("/usr/bin/python3", ["-m", "atlas", "serve"], "/var/rs")
    assert "ExecStart=/usr/bin/python3 -m atlas serve" in u


def test_systemd_unit_extra_env():
    u = winsvc.systemd_unit("/usr/bin/atlas", ["serve"], "/var/rs",
                            extra_env={"ATLAS_MOUNT_ROOTS": "/a,/b",
                                       "TESSDATA_PREFIX": "/opt/tessdata"})
    assert "Environment=ATLAS_MOUNT_ROOTS=/a,/b" in u
    assert "Environment=TESSDATA_PREFIX=/opt/tessdata" in u


# --- winsw_xml (čista funkcija) -------------------------------------------

def test_winsw_xml_structure():
    xml = winsvc.winsw_xml("C:/Program Files/atlas/atlas.exe", ["serve"], "C:/data", 8443)
    assert "<id>ATLAS</id>" in xml
    assert "<name>ATLAS</name>" in xml
    assert "<executable>C:/Program Files/atlas/atlas.exe</executable>" in xml
    assert "<arguments>serve</arguments>" in xml
    from pathlib import Path
    assert str(Path("C:/data") / "logs") in xml   # isti izraz kao u winsw_xml — identično na svim OS-ima
    assert xml.count('action="restart"') == 3
    assert 'action="none"' in xml
    assert "<stoptimeout>15 sec</stoptimeout>" in xml
    assert "NT AUTHORITY\\LocalService" in xml
    assert "ATLAS_DATA_DIR" in xml
    assert "8443" in xml


def test_winsw_xml_split_executable_and_args():
    """CRITICAL fix: python fallback (exe, args) razdvojeni -> <executable>
    je STVARNA putanja do jedne datoteke, <arguments> nosi '-m atlas serve'
    (review nalaz: spojeno u jedan string WinSW traži nepostojeću datoteku
    tog imena pa servis nikad ne starta kad atlas nije na PATH-u)."""
    xml = winsvc.winsw_xml("C:/Python311/python.exe", ["-m", "atlas", "serve"], "C:/data", 8443)
    assert "<executable>C:/Python311/python.exe</executable>" in xml
    assert "<arguments>-m atlas serve</arguments>" in xml


def test_winsw_xml_extra_env():
    xml = winsvc.winsw_xml("C:/atlas.exe", ["serve"], "C:/data", 8443,
                           extra_env={"ATLAS_MOUNT_ROOTS": "C:/a,C:/b",
                                      "TESSDATA_PREFIX": "C:/tess"})
    assert '<env name="ATLAS_MOUNT_ROOTS" value="C:/a,C:/b"/>' in xml
    assert '<env name="TESSDATA_PREFIX" value="C:/tess"/>' in xml


# --- download_winsw ---------------------------------------------------

def test_download_winsw_existing_file_skips_network(tmp_path):
    dest = tmp_path / "atlas-service.exe"
    dest.write_bytes(b"already-here")
    called = []

    def fake_urlopen(url, timeout=None):
        called.append(url)
        raise AssertionError("ne smije zvati mrezu kad file vec postoji")
    assert winsvc.download_winsw(dest, urlopen=fake_urlopen) is True
    assert not called
    assert dest.read_bytes() == b"already-here"


def test_download_winsw_success_is_atomic(tmp_path):
    dest = tmp_path / "sub" / "atlas-service.exe"

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"exe-bytes"

    def fake_urlopen(url, timeout=None):
        assert url == winsvc.WINSW_URL
        return _Resp()
    assert winsvc.download_winsw(dest, urlopen=fake_urlopen) is True
    assert dest.read_bytes() == b"exe-bytes"
    assert not dest.with_suffix(dest.suffix + ".tmp").exists()


def test_download_winsw_error_returns_false(tmp_path):
    dest = tmp_path / "atlas-service.exe"

    def fake_urlopen(url, timeout=None):
        raise OSError("mreza nedostupna")
    lines = []
    assert winsvc.download_winsw(dest, urlopen=fake_urlopen, out=lines.append) is False
    assert not dest.exists()
    assert any("nije uspio" in l for l in lines)


# --- install_service: Windows (WinSW + firewall + ACL) --------------------

def _stub_winsw_exe(data_dir):
    """Pripremi 'vec skinut' WinSW exe da install testovi ne idu na mrezu."""
    service_dir = data_dir / "service"
    service_dir.mkdir()
    exe = service_dir / "atlas-service.exe"
    exe.write_bytes(b"stub")
    return exe


def test_install_service_windows_full_flow(tmp_path, monkeypatch):
    _stub_winsw_exe(tmp_path)
    calls = []
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: calls.append(cmd) or (0, "", ""))
    lines = []
    result = winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lines.append)
    assert result is True
    xml_path = tmp_path / "service" / "atlas-service.xml"
    assert xml_path.exists()
    assert "ATLAS" in xml_path.read_text(encoding="utf-8")
    flat = [" ".join(c) for c in calls]
    # 1. idempotentni status-check (sc.exe query) 2. install 3. start 4. firewall 5. acl
    assert len(calls) == 5
    assert calls[0] == ["sc.exe", "query", "ATLAS"]
    assert flat[1].endswith("atlas-service.exe install")
    assert flat[2].endswith("atlas-service.exe start")
    assert any("advfirewall" in f and "8443" in f for f in flat)
    assert any("icacls" in f for f in flat)
    assert any("BIOS" in l for l in lines)


def test_install_service_windows_stops_on_winsw_install_error(tmp_path, monkeypatch):
    _stub_winsw_exe(tmp_path)
    calls = []

    def _run(cmd, timeout=60, **kw):
        calls.append(cmd)
        return (1, "", "pristup odbijen")
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", _run)
    lines = []
    result = winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lines.append)
    assert result is False
    assert len(calls) == 2                 # status-check (rc!=0 -> not-installed) pa install-pad
    assert calls[1][-1] == "install"
    assert any("pristup odbijen" in l for l in lines)


def test_install_service_windows_stops_on_download_error(tmp_path, monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    called = []
    monkeypatch.setattr(winsvc, "run_isolated", lambda *a, **k: called.append(1) or (0, "", ""))

    def fake_urlopen(url, timeout=None):
        raise OSError("nema neta")
    lines = []
    result = winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443,
                                     urlopen=fake_urlopen, out=lines.append)
    assert result is False
    assert not called                      # download pada PRIJE ikakvog run_isolated poziva
    assert any("download nije uspio" in l for l in lines)


def test_install_service_windows_idempotent_uninstalls_existing_first(tmp_path, monkeypatch):
    """IMPORTANT 4: servis već postoji (bilo koje stanje) -> ukloni prije
    reinstalacije umjesto da WinSW install padne na 'već postoji'."""
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    calls = []

    def _run(cmd, timeout=60, **kw):
        calls.append(cmd)
        if cmd[:2] == ["sc.exe", "query"]:
            return (0, "STATE : 4 RUNNING", "")
        return (0, "", "")
    monkeypatch.setattr(winsvc, "run_isolated", _run)
    lines = []
    result = winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lines.append)
    assert result is True
    flat = [" ".join(c) for c in calls]
    assert any(f.endswith("stop") for f in flat)
    assert any(f.endswith("uninstall") for f in flat)
    assert any("već postoji" in l for l in lines)


def test_install_service_windows_warns_when_exe_under_users(tmp_path, monkeypatch):
    """IMPORTANT 6: exe pod C:\\Users\\... -> upozorenje (LocalService, 1053), bez blokade."""
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    lines = []
    result = winsvc.install_service(r"C:\Users\matej\atlas.exe", ["serve"], str(tmp_path), 8443,
                                     out=lines.append)
    assert result is True
    assert any("LocalService" in l and "1053" in l for l in lines)


def test_install_service_windows_no_warning_for_system_path(tmp_path, monkeypatch):
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    lines = []
    winsvc.install_service(r"C:\Program Files\ATLAS\atlas.exe", ["serve"], str(tmp_path), 8443,
                           out=lines.append)
    assert not any("1053" in l for l in lines)


def test_install_service_windows_writes_mount_roots_env(tmp_path, monkeypatch):
    """IMPORTANT 2: folders su fail-closed bez ATLAS_MOUNT_ROOTS — servis mora
    dobiti registrirane mape u vlastitom env-u, ne u wizardovom os.environ."""
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    lines = []
    winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443,
                           mount_roots=["C:/Users/x/Dokumenti", "C:/Data"], out=lines.append)
    xml = (tmp_path / "service" / "atlas-service.xml").read_text(encoding="utf-8")
    assert "ATLAS_MOUNT_ROOTS" in xml
    assert "C:/Users/x/Dokumenti,C:/Data" in xml
    assert any("ponovni" in l and "servis install" in l for l in lines)


def test_install_service_windows_writes_tessdata_prefix_from_env(tmp_path, monkeypatch):
    """IMPORTANT 3: LocalService nema HKCU tekućeg korisnika — TESSDATA_PREFIX
    mora ući u servisni env eksplicitno."""
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    monkeypatch.setenv("TESSDATA_PREFIX", "C:/tess")
    winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lambda *_: None)
    xml = (tmp_path / "service" / "atlas-service.xml").read_text(encoding="utf-8")
    assert "TESSDATA_PREFIX" in xml and "C:/tess" in xml


def test_install_service_windows_tessdata_prefix_from_registry(tmp_path, monkeypatch):
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(winsvc.winpath, "get_user_env", lambda name: "C:/registry-tess")
    winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lambda *_: None)
    xml = (tmp_path / "service" / "atlas-service.xml").read_text(encoding="utf-8")
    assert "C:/registry-tess" in xml


def test_install_service_windows_tessdata_from_data_dir_fallback(tmp_path, monkeypatch):
    _stub_winsw_exe(tmp_path)
    (tmp_path / "tessdata").mkdir()
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(winsvc.winpath, "get_user_env", lambda name: None)
    winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lambda *_: None)
    xml = (tmp_path / "service" / "atlas-service.xml").read_text(encoding="utf-8")
    assert str(tmp_path / "tessdata") in xml


def test_install_service_windows_no_tessdata_env_when_absent(tmp_path, monkeypatch):
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(winsvc.winpath, "get_user_env", lambda name: None)
    winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lambda *_: None)
    xml = (tmp_path / "service" / "atlas-service.xml").read_text(encoding="utf-8")
    assert "TESSDATA_PREFIX" not in xml


def test_install_service_windows_firewall_fails_but_service_running(tmp_path, monkeypatch):
    """IMPORTANT 9: WinSW install/start uspiju, firewall/ACL padne -> servis i
    dalje 'radi', poruka to mora reći (ne 'nije instaliran'); vraća True."""
    _stub_winsw_exe(tmp_path)
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")

    def _run(cmd, timeout=60, **kw):
        if "advfirewall" in cmd:
            return (1, "", "pristup odbijen")
        return (0, "", "")
    monkeypatch.setattr(winsvc, "run_isolated", _run)
    lines = []
    result = winsvc.install_service("C:/rs.exe", ["serve"], str(tmp_path), 8443, out=lines.append)
    assert result is True
    text = "\n".join(lines)
    assert "RADI" in text
    assert "nije instaliran" not in text
    assert "✓ Servis ATLAS instaliran i pokrenut" not in text


# --- install_service: ne-Windows (systemd) --------------------------------

def test_install_service_non_windows_writes_unit_and_enables(tmp_path, monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    calls = []
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: calls.append(cmd) or (0, "", ""))
    unit_path = tmp_path / "atlas.service"
    lines = []
    result = winsvc.install_service("/usr/bin/atlas", ["serve"], "/var/rs", 8443,
                                     unit_path=str(unit_path), out=lines.append)
    assert result is True
    assert unit_path.exists()
    assert "[Service]" in unit_path.read_text(encoding="utf-8")
    assert calls == [["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", "atlas"]]


def test_install_service_non_windows_permission_error(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")

    def _raise(self, *a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(winsvc.Path, "write_text", _raise)
    called = []
    monkeypatch.setattr(winsvc, "run_isolated", lambda *a, **k: called.append(1) or (0, "", ""))
    lines = []
    result = winsvc.install_service("/usr/bin/atlas", ["serve"], "/var/rs", 8443, out=lines.append)
    assert result is False
    assert not called
    assert any("[Unit]" in l or "systemd" in l.lower() or "zapisati" in l for l in lines)


def test_install_service_non_windows_oserror_not_just_permission(tmp_path, monkeypatch):
    """IMPORTANT 5: macOS nema /etc/systemd/system -> FileNotFoundError
    (OSError, ne PermissionError) — ne smije propagirati i rušiti wizard."""
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Darwin")
    called = []
    monkeypatch.setattr(winsvc, "run_isolated", lambda *a, **k: called.append(1) or (0, "", ""))
    missing_unit = tmp_path / "nema" / "takve" / "mape" / "atlas.service"
    lines = []
    result = winsvc.install_service("/usr/bin/atlas", ["serve"], "/var/rs", 8443,
                                     unit_path=str(missing_unit), out=lines.append)
    assert result is False
    assert not called
    assert any("[Unit]" in l or "systemd" in l.lower() or "zapisati" in l for l in lines)


# --- uninstall_service -----------------------------------------------------

def test_uninstall_service_windows_success(tmp_path, monkeypatch):
    _stub_winsw_exe(tmp_path)
    calls = []
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: calls.append(cmd) or (0, "", ""))
    lines = []
    assert winsvc.uninstall_service(str(tmp_path), out=lines.append) is True
    assert len(calls) == 2
    assert calls[0][-1] == "stop"
    assert calls[1][-1] == "uninstall"


def test_uninstall_service_windows_missing_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    lines = []
    assert winsvc.uninstall_service(str(tmp_path), out=lines.append) is False
    assert any("ne postoji" in l for l in lines)


def test_uninstall_service_posix_prints_instructions(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    lines = []
    assert winsvc.uninstall_service("/var/rs", out=lines.append,
                                     unit_path="/etc/systemd/system/atlas.service") is False
    assert any("systemctl" in l for l in lines)


# --- service_status ---------------------------------------------------

def test_service_status_windows_running(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, "STATE : 4 RUNNING", ""))
    assert winsvc.service_status() == "running"


def test_service_status_windows_start_pending(monkeypatch):
    """MINOR 7: prijelazno stanje START_PENDING ne smije izgledati kao 'nema servisa'."""
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, "STATE : 2 START_PENDING", ""))
    assert winsvc.service_status() == "running"


def test_service_status_windows_stop_pending(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, "STATE : 3 STOP_PENDING", ""))
    assert winsvc.service_status() == "stopped"


def test_service_status_windows_stopped(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, "STATE : 1 STOPPED", ""))
    assert winsvc.service_status() == "stopped"


def test_service_status_windows_not_installed(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (1, "", "openservice failed"))
    assert winsvc.service_status() == "not-installed"


def test_service_status_posix_running(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (0, "active\n", ""))
    assert winsvc.service_status() == "running"


def test_service_status_posix_inactive(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (3, "inactive\n", ""))
    assert winsvc.service_status() == "inactive"


def test_service_status_posix_not_installed(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: (4, "unknown\n", ""))
    assert winsvc.service_status() == "not-installed"
