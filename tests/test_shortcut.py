"""shortcut: prečac na radnoj površini — sadržaj datoteka, bez pravih procesa."""
import os
import plistlib

from atlas.ops import shortcut


def test_desktop_dir_posix(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    (tmp_path / "Desktop").mkdir()
    assert shortcut._desktop_dir() == str(tmp_path / "Desktop")


def test_desktop_dir_xdg(tmp_path, monkeypatch):
    d = tmp_path / "Radna"
    d.mkdir()
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(d))
    assert shortcut._desktop_dir() == str(d)


def test_desktop_dir_nema(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    assert shortcut._desktop_dir() is None


def test_linux_desktop_datoteka(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    (tmp_path / "Desktop").mkdir()
    lines = []
    ok = shortcut.create_desktop_shortcut("https://x:8443", out=lines.append,
                                          system=lambda: "Linux")
    assert ok is True
    f = tmp_path / "Desktop" / "ATLAS.desktop"
    text = f.read_text()
    assert "Exec=xdg-open 'https://x:8443'" in text
    assert "Type=Application" in text
    assert os.access(f, os.X_OK)
    assert any("✓" in l for l in lines)


def test_macos_webloc(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    (tmp_path / "Desktop").mkdir()
    ok = shortcut.create_desktop_shortcut("https://x:8443", out=lambda *_: None,
                                          system=lambda: "Darwin")
    assert ok is True
    data = plistlib.loads((tmp_path / "Desktop" / "ATLAS.webloc").read_bytes())
    assert data == {"URL": "https://x:8443"}


def test_windows_lnk_preko_powershella(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(shortcut, "_browser_exe",
                        lambda: r"C:\Program Files\Edge\msedge.exe")
    calls = []
    ok = shortcut.create_desktop_shortcut("https://x:8443", out=lambda *_: None,
                                          system=lambda: "Windows",
                                          run=lambda cmd: calls.append(cmd) or 0)
    assert ok is True
    assert calls and calls[0][0] == "powershell"
    script = calls[0][-1]
    assert "msedge.exe" in script and "--app=https://x:8443" in script
    assert "ATLAS.lnk" in script


def test_windows_url_fallback_bez_browsera(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(shortcut, "_browser_exe", lambda: None)
    ok = shortcut.create_desktop_shortcut("https://x:8443", out=lambda *_: None,
                                          system=lambda: "Windows",
                                          run=lambda cmd: 1)
    assert ok is True
    text = (tmp_path / "Desktop" / "ATLAS.url").read_text()
    assert "[InternetShortcut]" in text and "URL=https://x:8443" in text


def test_windows_powershell_pad_ide_na_url(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "A"))
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(shortcut, "_browser_exe", lambda: r"C:\E\msedge.exe")
    ok = shortcut.create_desktop_shortcut("https://x:8443", out=lambda *_: None,
                                          system=lambda: "Windows",
                                          run=lambda cmd: 1)   # PS pao
    assert ok is True
    assert (tmp_path / "Desktop" / "ATLAS.url").exists()


def test_bez_desktopa_poruka_bez_pada(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    lines = []
    ok = shortcut.create_desktop_shortcut("https://x", out=lines.append,
                                          system=lambda: "Linux")
    assert ok is False
    assert any("⚠" in l for l in lines)
