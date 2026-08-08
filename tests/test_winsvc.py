# tests/test_winsvc.py
from atlas.ops import winsvc


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
    u = winsvc.systemd_unit("/usr/bin/atlas", "/var/rs")
    assert "[Service]" in u and "Restart=on-failure" in u and "/usr/bin/atlas serve" in u


# --- winsw_xml (čista funkcija) -------------------------------------------

def test_winsw_xml_structure():
    xml = winsvc.winsw_xml("C:/Program Files/atlas/atlas.exe", "C:/data", 8443)
    assert "<id>ATLAS</id>" in xml
    assert "<name>ATLAS</name>" in xml
    assert "<executable>C:/Program Files/atlas/atlas.exe</executable>" in xml
    assert "<arguments>serve</arguments>" in xml
    assert "C:/data/logs" in xml
    assert xml.count('action="restart"') == 3
    assert 'action="none"' in xml
    assert "<stoptimeout>15 sec</stoptimeout>" in xml
    assert "NT AUTHORITY\\LocalService" in xml
    assert "ATLAS_DATA_DIR" in xml
    assert "8443" in xml


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
    result = winsvc.install_service("C:/rs.exe", str(tmp_path), 8443, out=lines.append)
    assert result is True
    xml_path = tmp_path / "service" / "atlas-service.xml"
    assert xml_path.exists()
    assert "ATLAS" in xml_path.read_text(encoding="utf-8")
    flat = [" ".join(c) for c in calls]
    assert len(calls) == 4
    assert flat[0].endswith("atlas-service.exe install")
    assert flat[1].endswith("atlas-service.exe start")
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
    result = winsvc.install_service("C:/rs.exe", str(tmp_path), 8443, out=lines.append)
    assert result is False
    assert len(calls) == 1
    assert any("pristup odbijen" in l for l in lines)


def test_install_service_windows_stops_on_download_error(tmp_path, monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    called = []
    monkeypatch.setattr(winsvc, "run_isolated", lambda *a, **k: called.append(1) or (0, "", ""))

    def fake_urlopen(url, timeout=None):
        raise OSError("nema neta")
    lines = []
    result = winsvc.install_service("C:/rs.exe", str(tmp_path), 8443,
                                     urlopen=fake_urlopen, out=lines.append)
    assert result is False
    assert not called
    assert any("download nije uspio" in l for l in lines)


# --- install_service: ne-Windows (systemd) --------------------------------

def test_install_service_non_windows_writes_unit_and_enables(tmp_path, monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    calls = []
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: calls.append(cmd) or (0, "", ""))
    unit_path = tmp_path / "atlas.service"
    lines = []
    result = winsvc.install_service("/usr/bin/atlas", "/var/rs", 8443,
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
    result = winsvc.install_service("/usr/bin/atlas", "/var/rs", 8443, out=lines.append)
    assert result is False
    assert not called
    assert any("[Unit]" in l or "systemd" in l.lower() or "Nemam prava" in l for l in lines)


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
