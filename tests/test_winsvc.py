# tests/test_winsvc.py  (novi)
from ragspine.ops import winsvc


def test_service_commands_shapes():
    cmds = winsvc.service_commands("C:/rs/ragspine.exe", "C:/data", 8443)
    assert all(isinstance(c, list) for c in cmds)
    flat = [" ".join(c) for c in cmds]
    assert any("sc.exe" in f and "create" in f and "LocalService" in f for f in flat)
    assert any("failure" in f for f in flat)
    assert any("advfirewall" in f and "8443" in f for f in flat)
    assert any("icacls" in f and "C:/data" in f for f in flat)
    # Provjeri sc.exe tokenizaciju: ključ= i vrijednost kao odvojeni elementi
    create_cmd = cmds[0]
    assert "binPath=" in create_cmd
    binpath_idx = create_cmd.index("binPath=")
    assert create_cmd[binpath_idx + 1] == "C:/rs/ragspine.exe serve"
    assert "obj=" in create_cmd
    obj_idx = create_cmd.index("obj=")
    assert create_cmd[obj_idx + 1] == "NT AUTHORITY\\LocalService"


def test_service_commands_spaced_paths():
    """Spaced paths trebaju biti dio vrijednosti, ne kao odvojeni elementi."""
    cmds = winsvc.service_commands("C:/Program Files/rs.exe", "C:/data", 8443)
    create_cmd = cmds[0]
    binpath_idx = create_cmd.index("binPath=")
    assert create_cmd[binpath_idx + 1] == "C:/Program Files/rs.exe serve"


def test_install_service_windows_executes_all(monkeypatch):
    calls = []
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: calls.append(cmd) or (0, "", ""))
    assert winsvc.install_service("C:/rs.exe", "C:/data", 8443, out=lambda *_: None) is True
    assert len(calls) == len(winsvc.service_commands("C:/rs.exe", "C:/data", 8443))


def test_install_service_stops_on_error(monkeypatch):
    calls = []

    def _run(cmd, timeout=60, **kw):
        calls.append(cmd)
        return (1 if len(calls) == 2 else 0, "", "pristup odbijen")
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", _run)
    lines = []
    assert winsvc.install_service("C:/rs.exe", "C:/data", 8443, out=lines.append) is False
    assert len(calls) == 2                          # stao na grešci
    assert any("pristup odbijen" in l for l in lines)


def test_install_service_non_windows_prints_systemd(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    called = []
    monkeypatch.setattr(winsvc, "run_isolated", lambda *a, **k: called.append(1) or (0, "", ""))
    lines = []
    assert winsvc.install_service("/usr/bin/ragspine", "/var/rs", 8443, out=lines.append) is False
    assert not called
    assert any("[Unit]" in l or "systemd" in l.lower() for l in lines)


def test_systemd_unit_content():
    u = winsvc.systemd_unit("/usr/bin/ragspine", "/var/rs")
    assert "[Service]" in u and "Restart=on-failure" in u and "/usr/bin/ragspine serve" in u
