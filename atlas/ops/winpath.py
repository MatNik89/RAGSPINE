"""Windows PATH/env pomagala: refresh iz registryja bez restarta terminala
(E2E nalaz: winget instalira, a tekući proces drži stari PATH), poznate
instalacijske lokacije (UB-Mannheim Tesseract NE dodaje PATH) i trajni
upis u user Environment (winreg, NE setx — setx truncira na 1024 znaka).
Registry funkcije su no-op izvan Windowsa."""
import os
import shutil

KNOWN_LOCATIONS = {
    "tesseract": [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"],
    "ollama": [r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
               r"C:\Program Files\Ollama\ollama.exe"],
}


def _merge_path(current: str, machine: str, user: str) -> str:
    """Spoji PATH-ove bez duplikata (case-insensitive po segmentu);
    redoslijed: machine, user, pa postojeći current."""
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
    """Pročitaj HKLM/HKCU Environment PATH i osvježi os.environ — 'Provjeri
    ponovno' u wizardu radi bez restarta terminala. Ne-Windows: False."""
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
    """shutil.which pa poznate instalacijske lokacije."""
    hit = shutil.which(key)
    if hit:
        return hit
    for cand in KNOWN_LOCATIONS.get(key, []):
        p = os.path.expandvars(cand)
        if os.path.isfile(p):
            return p
    return None


def persist_user_env(name: str, value: str) -> bool:
    """Trajni upis u HKCU\\Environment + WM_SETTINGCHANGE broadcast (novi
    procesi vide odmah). Čuva postojeći registry tip vrijednosti."""
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
    """Dodaj mapu u user PATH (registry, bez brisanja postojećeg) i u
    os.environ tekućeg procesa. Idempotentno."""
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
