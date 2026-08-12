"""Windows PATH/env helpers: refresh from the registry without restarting the
terminal (E2E finding: winget installs, but the current process holds the old
PATH), known install locations (UB-Mannheim Tesseract does NOT add to PATH),
and a persistent write to the user Environment (winreg, NOT setx — setx
truncates at 1024 characters). Registry functions are a no-op outside
Windows."""
import os
import shutil

KNOWN_LOCATIONS = {
    "tesseract": [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"],
    "ollama": [r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
               r"C:\Program Files\Ollama\ollama.exe"],
}


def _merge_path(current: str, machine: str, user: str) -> str:
    """Merge PATHs without duplicates (case-insensitive per segment); order:
    machine, user, then the existing current."""
    seen: set[str] = set()
    merged: list[str] = []
    for chunk in (machine, user, current):
        for p in chunk.split(os.pathsep):
            p = p.strip()
            if p and p.lower() not in seen:
                seen.add(p.lower())
                merged.append(p)
    return os.pathsep.join(merged)


def refresh_path_from_registry() -> bool:
    """Read the HKLM/HKCU Environment PATH and refresh os.environ — 'Check
    again' in the wizard works without restarting the terminal. Non-Windows:
    False."""
    if os.name != "nt":
        return False
    import winreg

    def _read(root, sub):
        try:
            with winreg.OpenKey(root, sub) as k:
                val, _t = winreg.QueryValueEx(k, "Path")
                return os.path.expandvars(val)
        except OSError:
            return ""

    machine = _read(winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user = _read(winreg.HKEY_CURRENT_USER, "Environment")
    os.environ["PATH"] = _merge_path(os.environ.get("PATH", ""), machine, user)
    return True


def find_binary(key: str) -> str | None:
    """shutil.which, then known install locations."""
    hit = shutil.which(key)
    if hit:
        return hit
    for cand in KNOWN_LOCATIONS.get(key, []):
        p = os.path.expandvars(cand)
        if os.path.isfile(p):
            return p
    return None


def get_user_env(name: str) -> str | None:
    """Read an HKCU\\Environment value (e.g. TESSDATA_PREFIX set persistently
    in a previous session via persist_user_env) — a service under LocalService
    has a different logon session and does not see the current os.environ, so it
    must be read explicitly from the registry. Non-Windows / missing: None."""
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            val, _t = winreg.QueryValueEx(k, name)
            return val
    except OSError:
        return None


def persist_user_env(name: str, value: str) -> bool:
    """Persistent write to HKCU\\Environment + WM_SETTINGCHANGE broadcast (new
    processes see it immediately). Preserves the existing registry value
    type."""
    if os.name != "nt":
        return False
    import ctypes
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as k:
        try:
            _old, typ = winreg.QueryValueEx(k, name)
        except OSError:
            typ = winreg.REG_EXPAND_SZ
        winreg.SetValueEx(k, name, 0, typ, value)
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x1A, 0, "Environment", 0x2, 5000, None)   # HWND_BROADCAST, WM_SETTINGCHANGE
    return True


def append_user_path(directory: str) -> bool:
    """Add a directory to the user PATH (registry, without deleting the
    existing one) and to os.environ of the current process. Idempotent."""
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            cur, _t = winreg.QueryValueEx(k, "Path")
    except OSError:
        cur = ""
    parts = [p for p in cur.split(os.pathsep) if p.strip()]
    if directory.lower() not in (p.lower() for p in parts):
        persist_user_env("Path", os.pathsep.join(parts + [directory]))
    env_parts = os.environ.get("PATH", "").split(os.pathsep)
    if directory.lower() not in (p.lower() for p in env_parts):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + directory
    return True
