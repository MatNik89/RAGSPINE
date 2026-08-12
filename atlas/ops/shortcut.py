"""Desktop shortcut (E2E: user closed the app window and could not find
their way back). Windows .lnk (app window) with a .url fallback, Linux
.desktop, macOS .webloc. Subprocess is injectable — tests run without real
processes."""
import os
import platform
import plistlib
import stat
import subprocess

_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _browser_exe() -> str | None:
    """Full path to Edge/Chrome for the --app window (they are not on PATH)."""
    for p in _BROWSERS:
        if os.path.isfile(p):
            return p
    return None


def _desktop_dir() -> str | None:
    """Desktop: XDG_DESKTOP_DIR -> USERPROFILE\\Desktop -> ~/Desktop.
    None when it does not exist (OneDrive redirections and the like — better
    to skip than to guess)."""
    xdg = os.environ.get("XDG_DESKTOP_DIR")
    if xdg and os.path.isdir(xdg):
        return xdg
    for base in (os.environ.get("USERPROFILE"), os.path.expanduser("~")):
        if base:
            d = os.path.join(base, "Desktop")
            if os.path.isdir(d):
                return d
    return None


def _write_url_file(path: str, url: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"[InternetShortcut]\nURL={url}\n")


def create_desktop_shortcut(url: str, *, name: str = "ATLAS", out=print,
                            run=None, system=platform.system) -> bool:
    """Create a desktop shortcut. True = created (any variant), False = no
    desktop or the write failed."""
    if run is None:
        run = lambda cmd: subprocess.call(  # pragma: no cover - thin wrapper
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    desk = _desktop_dir()
    if not desk:
        out("⚠ Radna površina nije pronađena — prečac preskočen "
            f"(otvaraj ručno: {url}).")
        return False
    try:
        osname = system()
        if osname == "Windows":
            return _windows_shortcut(desk, url, name, out, run)
        if osname == "Darwin":
            with open(os.path.join(desk, f"{name}.webloc"), "wb") as f:
                plistlib.dump({"URL": url}, f)
        else:
            p = os.path.join(desk, f"{name}.desktop")
            with open(p, "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\n"
                        f"Name={name}\nType=Application\n"
                        f"Exec=xdg-open '{url}'\n"
                        "Icon=web-browser\nTerminal=false\n")
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP)
        out(f"✓ Prečac na radnoj površini: {name}")
        return True
    except OSError as e:
        out(f"⚠ Prečac nije napravljen ({e}) — otvaraj ručno: {url}")
        return False


def _windows_shortcut(desk: str, url: str, name: str, out, run) -> bool:
    """`.lnk` with an app window (WScript.Shell via PowerShell); browser not
    found or PS failed -> `.url` (default browser). A Start Menu copy alongside
    the .lnk — the user can also find it through Start search."""
    browser = _browser_exe()
    if browser:
        lnk = os.path.join(desk, f"{name}.lnk")
        targets = [lnk]
        appdata = os.environ.get("APPDATA")
        if appdata:
            sm = os.path.join(appdata, "Microsoft", "Windows",
                              "Start Menu", "Programs")
            os.makedirs(sm, exist_ok=True)
            targets.append(os.path.join(sm, f"{name}.lnk"))
        script = "; ".join(
            "$ws = New-Object -ComObject WScript.Shell"
            f"; $s = $ws.CreateShortcut('{t}')"
            f"; $s.TargetPath = '{browser}'"
            f"; $s.Arguments = '--app={url}'"
            f"; $s.Save()" for t in targets)
        if run(["powershell", "-NoProfile", "-NonInteractive",
                "-Command", script]) == 0:
            out(f"✓ Prečac (app-prozor): {name}.lnk — radna površina i Start Menu")
            return True
    _write_url_file(os.path.join(desk, f"{name}.url"), url)
    out(f"✓ Prečac: {name}.url (zadani preglednik)")
    return True
