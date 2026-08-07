# TUI face-lift 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Folder picker na stranici 5, živi izlaz podprocesa (winget/ollama pull), PATH refresh bez restarta, Tesseract auto-install s hrv paketom, install.ps1 uskladba.

**Architecture:** Tri nova/proširena sloja: `subproc.run_streaming` (živi stream s \r), `atlas/ops/winpath.py` (registry PATH/env, poznate lokacije), `atlas/ops/folder_picker.py` (browsanje nad tui_curses.radiolist). Preflight ih veže (winget tok, traineddata, requirements); wizard stranica 5 prelazi na picker. Sve mrežno/registry/subprocess injektabilno — testovi bez mreže, stdina i pravih subprocessa.

**Tech Stack:** Python 3.11+ stdlib (winreg/ctypes tanki Windows-only slojevi), pytest.

## Global Constraints

- Hrvatski latinica s dijakriticima; NIKAD ćirilica.
- Bez novih ovisnosti.
- Testovi bez mreže/stdina/pravih subprocessa/TTY-ja; curses nikad u testovima.
- Puni suite u prvom planu prije svakog commita (`python -m pytest -q`, prije grane: 1181 passed, 1 skipped).
- Hrvatske konvencionalne commit poruke + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Potpisi wizard stranica se NE mijenjaju.
- NE dodavati testove koji globalno patchaju os.name na "posix" pa instanciraju Path (Windows CI INTERNALERROR lekcija).

---

### Task 1: subproc.run_streaming — živi izlaz s poštivanjem \r

**Files:**
- Modify: `atlas/core/subproc.py`
- Test: `tests/test_subproc.py` (dodaj)

**Interfaces:**
- Produces: `run_streaming(cmd, *, timeout=600, out=print, popen=subprocess.Popen) -> int` — returncode; -9 na timeout. stdout+stderr spojeni. `\r` segmenti: na pravom TTY-ju (out is print i stdout TTY) osvježavaju redak u mjestu; injektirani out dobiva svaki segment kao redak.
- Produces: `_kill_tree(proc)` — izvučen iz run_isolated (oba ga koriste).

- [ ] **Step 1: failing testovi** (dodaj u `tests/test_subproc.py`):

```python
import io

from atlas.core import subproc


class _FakeProc:
    def __init__(self, data: bytes, rc: int = 0):
        self.stdout = io.BytesIO(data)
        self.returncode = rc
        self.pid = 4242

    def wait(self, timeout=None):
        return self.returncode


def test_run_streaming_linije_lf():
    lines = []
    rc = subproc.run_streaming(["x"], out=lines.append,
                               popen=lambda cmd, **k: _FakeProc(b"prva\ndruga\n"))
    assert rc == 0
    assert lines == ["prva", "druga"]


def test_run_streaming_cr_segmenti_kao_retci():
    """Injektirani out: svaki \\r segment = zaseban redak (progress povijest)."""
    lines = []
    rc = subproc.run_streaming(
        ["x"], out=lines.append,
        popen=lambda cmd, **k: _FakeProc(b"pull 10%\rpull 50%\rpull 100%\ngotovo\n"))
    assert rc == 0
    assert lines == ["pull 10%", "pull 50%", "pull 100%", "gotovo"]


def test_run_streaming_returncode_i_prazni_redci():
    lines = []
    rc = subproc.run_streaming(["x"], out=lines.append,
                               popen=lambda cmd, **k: _FakeProc(b"\n\nx\n", rc=3))
    assert rc == 3
    assert lines == ["x"]


def test_run_streaming_utf8_replace():
    lines = []
    subproc.run_streaming(["x"], out=lines.append,
                          popen=lambda cmd, **k: _FakeProc(b"\xff\xfezlo\n"))
    assert any("zlo" in l for l in lines)
```

- [ ] **Step 2: run — FAIL** (`python -m pytest tests/test_subproc.py -q`)

- [ ] **Step 3: implementacija u `atlas/core/subproc.py`**

Prvo izvuci kill-stablo iz `run_isolated` u zajedničku funkciju (tijelo je
POSTOJEĆI kod iz run_isolated `except TimeoutExpired` bloka — premjesti, ne
mijenjaj semantiku):

```python
def _kill_tree(proc) -> None:
    """Ubij proces i CIJELO stablo (Windows: taskkill /T /F; POSIX: killpg)."""
    try:
        if os.name == "posix" and hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        elif os.name == "nt":
            tk = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                capture_output=True)
            if tk.returncode != 0:
                proc.kill()
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass
```

`run_isolated` u timeout grani sada zove `_kill_tree(proc)` (obriši
duplicirani inline kod). Zatim dodaj:

```python
def run_streaming(cmd, *, timeout: int = 600, out=print,
                  popen=subprocess.Popen) -> int:
    """Pokreni proces i prosljeđuj izlaz UŽIVO (winget/instalacije — kraj
    mrtvog ekrana, E2E nalaz). Poštuje \r: na pravom TTY-ju redak se
    osvježava u mjestu; injektirani out dobiva segmente kao retke.
    stdout+stderr spojeni; utf-8 errors=replace. -9 na timeout."""
    import sys
    import threading
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    timed_out = threading.Event()

    def _on_timeout():
        timed_out.set()
        _kill_tree(proc)

    timer = threading.Timer(timeout, _on_timeout)
    timer.start()
    tty = out is print and sys.stdout.isatty()
    inplace = False

    def _emit(segment: str, cr: bool) -> None:
        nonlocal inplace
        if not segment:
            return
        if tty and cr:
            sys.stdout.write("\r  " + segment + "        ")
            sys.stdout.flush()
            inplace = True
            return
        if tty and inplace:
            sys.stdout.write("\n")
            inplace = False
        out(segment)

    try:
        buf = b""
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in (b"\n", b"\r"):
                _emit(buf.decode("utf-8", errors="replace").rstrip(), ch == b"\r")
                buf = b""
            else:
                buf += ch
        _emit(buf.decode("utf-8", errors="replace").rstrip(), False)
        if inplace:
            sys.stdout.write("\n")
    finally:
        timer.cancel()
    if timed_out.is_set():
        return -9
    try:
        return proc.wait(timeout=15) or 0
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return -9
```

ponytail: čitanje bajt-po-bajt je sporo za megabajte izlaza — winget/pip
progres je kilobajti, dovoljno; nadogradnja = read(4096) s vlastitim
splitom kad zatreba.

- [ ] **Step 4: run + puni suite** (`python -m pytest tests/test_subproc.py -q` pa `python -m pytest -q` — run_isolated testovi moraju ostati zeleni nakon _kill_tree ekstrakcije)

- [ ] **Step 5: Commit**

```bash
git add atlas/core/subproc.py tests/test_subproc.py
git commit -m "feat(subproc): run_streaming — živi izlaz podprocesa s poštivanjem carriage returna"
```

---

### Task 2: winpath.py — PATH refresh, poznate lokacije, trajni user env

**Files:**
- Create: `atlas/ops/winpath.py`
- Test: `tests/test_winpath.py`

**Interfaces:**
- Produces: `_merge_path(current, machine, user) -> str`; `refresh_path_from_registry() -> bool` (ne-Windows: False); `find_binary(key) -> str | None`; `persist_user_env(name, value) -> bool` (ne-Windows: False); `append_user_path(directory) -> bool` (ne-Windows: False); `KNOWN_LOCATIONS: dict[str, list[str]]`.

- [ ] **Step 1: failing testovi**

`tests/test_winpath.py`:

```python
"""winpath: čisti merge/lookup dijelovi; registry sloj je Windows-only guard."""
import os
import shutil

from atlas.ops import winpath


def test_merge_path_dedup_i_redoslijed():
    got = winpath._merge_path("C;A", "A;B", "B;D")
    assert got == os.pathsep.join(["A", "B", "D", "C"])


def test_merge_path_case_insensitive_dedup():
    got = winpath._merge_path("", r"C:\Alat", r"c:\alat;X")
    assert got == os.pathsep.join([r"C:\Alat", "X"])


def test_merge_path_prazni_segmenti_ispadaju():
    assert winpath._merge_path(";;", "A;;B", "") == os.pathsep.join(["A", "B"])


def test_find_binary_which_ima_prednost(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda k: "/usr/bin/tess")
    assert winpath.find_binary("tesseract") == "/usr/bin/tess"


def test_find_binary_poznata_lokacija(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(shutil, "which", lambda k: None)
    monkeypatch.setitem(winpath.KNOWN_LOCATIONS, "tesseract", [str(exe)])
    assert winpath.find_binary("tesseract") == str(exe)


def test_find_binary_nema_nigdje(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda k: None)
    monkeypatch.setitem(winpath.KNOWN_LOCATIONS, "tesseract", ["/nema/toga"])
    assert winpath.find_binary("tesseract") is None
    assert winpath.find_binary("nepoznat-alat") is None


def test_registry_slojevi_su_noop_izvan_windowsa():
    if os.name == "nt":
        return   # na Windows CI ove funkcije stvarno diraju registry — preskoči
    assert winpath.refresh_path_from_registry() is False
    assert winpath.persist_user_env("X", "y") is False
    assert winpath.append_user_path("/tmp") is False
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: implementacija `atlas/ops/winpath.py`**

```python
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
```

- [ ] **Step 4: run + puni suite**

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/winpath.py tests/test_winpath.py
git commit -m "feat(winpath): PATH refresh iz registryja, poznate lokacije binarki, trajni user env upis"
```

---

### Task 3: preflight — winget tok (već instalirano, streaming, najava), traineddata, requirements, ollama_pull

**Files:**
- Modify: `atlas/ops/preflight.py` (install_via_winget, requirements tesseract blok, ollama_pull; novi ensure_traineddata; import winpath i run_streaming)
- Test: `tests/test_preflight.py` (dodaj/prilagodi — postojeći ollama_pull i install_via_winget testovi možda očekuju stari izlaz; prilagodi ih)

**Interfaces:**
- Consumes: `winpath.*` (Task 2), `subproc.run_streaming` (Task 1).
- Produces: `ensure_traineddata(langs=("hrv", "eng"), *, out=print, urlopen=urllib.request.urlopen) -> bool`; `install_via_winget` isti potpis, novo ponašanje; `ollama_pull` isti potpis (injektirani out: svakih 10 % umjesto 1 %).

- [ ] **Step 1: failing testovi** (dodaj u `tests/test_preflight.py`; usaglasi postojeće):

```python
def test_install_via_winget_vec_instalirano(monkeypatch):
    """Postojeća binarka → 'već instalirano', winget se NE zove."""
    from atlas.ops import preflight, winpath
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winpath, "find_binary", lambda k: r"C:\alat\ollama.exe")
    monkeypatch.setattr(preflight, "run_streaming",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("winget pozvan")))
    lines = []
    assert preflight.install_via_winget("ollama", out=lines.append) is True
    assert any("već instaliran" in l for l in lines)


def test_install_via_winget_streaming_i_najava(monkeypatch, tmp_path):
    from atlas.ops import preflight, winpath
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    exe = tmp_path / "ollama.exe"
    hits = iter([None, str(exe)])   # prije installa nema, poslije ima
    monkeypatch.setattr(winpath, "find_binary", lambda k: next(hits, str(exe)))
    monkeypatch.setattr(winpath, "refresh_path_from_registry", lambda: True)
    monkeypatch.setattr(winpath, "append_user_path", lambda d: True)
    monkeypatch.setattr(preflight.shutil, "which", lambda k: None)
    calls = []
    monkeypatch.setattr(preflight, "run_streaming",
                        lambda cmd, **k: calls.append(cmd) or 0)
    exe.write_bytes(b"x")
    lines = []
    assert preflight.install_via_winget("ollama", out=lines.append) is True
    assert calls and "winget" in calls[0][0]
    assert any("700 MB" in l for l in lines)      # najava veličine (E2E nalaz)


def test_install_via_winget_tesseract_zove_traineddata(monkeypatch, tmp_path):
    from atlas.ops import preflight, winpath
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))
    monkeypatch.setattr(preflight.shutil, "which", lambda k: str(exe))
    called = []
    monkeypatch.setattr(preflight, "ensure_traineddata",
                        lambda langs=("hrv", "eng"), out=print, urlopen=None:
                        called.append(langs) or True)
    assert preflight.install_via_winget("tesseract", out=lambda *_: None) is True
    assert called


def _resp(data: bytes):
    import io

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _R(data)


def test_ensure_traineddata_skida_u_tessdata_uz_exe(tmp_path, monkeypatch):
    from atlas.ops import preflight, winpath
    exe = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    (exe.parent / "tessdata").mkdir()
    (exe.parent / "tessdata" / "eng.traineddata").write_bytes(b"eng")
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))
    urls = []
    lines = []
    ok = preflight.ensure_traineddata(
        ("hrv", "eng"), out=lines.append,
        urlopen=lambda url, timeout=120: urls.append(url) or _resp(b"HRVDATA"))
    assert ok is True
    assert (exe.parent / "tessdata" / "hrv.traineddata").read_bytes() == b"HRVDATA"
    assert len(urls) == 1 and "hrv.traineddata" in urls[0]   # eng već postoji


def test_ensure_traineddata_fallback_na_data_dir(tmp_path, monkeypatch):
    """Program Files bez dozvole → data_dir/tessdata + TESSDATA_PREFIX;
    eng se KOPIRA iz primarne lokacije (TESSDATA_PREFIX je isključiv)."""
    from atlas.ops import preflight, winpath
    from atlas import config
    exe = tmp_path / "pf" / "tesseract.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    td = exe.parent / "tessdata"
    td.mkdir()
    (td / "eng.traineddata").write_bytes(b"ENG")
    datadir = tmp_path / "data"
    datadir.mkdir()
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))
    monkeypatch.setattr(config, "default_data_dir", lambda: str(datadir))
    monkeypatch.setattr(preflight, "_dir_writable", lambda d: d != td)
    persisted = []
    monkeypatch.setattr(winpath, "persist_user_env",
                        lambda n, v: persisted.append((n, v)) or True)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    ok = preflight.ensure_traineddata(
        ("hrv", "eng"), out=lambda *_: None,
        urlopen=lambda url, timeout=120: _resp(b"HRV"))
    assert ok is True
    dest = datadir / "tessdata"
    assert (dest / "hrv.traineddata").read_bytes() == b"HRV"
    assert (dest / "eng.traineddata").read_bytes() == b"ENG"     # kopiran
    assert os.environ["TESSDATA_PREFIX"] == str(dest)
    assert ("TESSDATA_PREFIX", str(dest)) in persisted


def test_ensure_traineddata_bez_tesseracta(monkeypatch):
    from atlas.ops import preflight, winpath
    monkeypatch.setattr(winpath, "find_binary", lambda k: None)
    assert preflight.ensure_traineddata(out=lambda *_: None,
                                        urlopen=None) is False


def test_ensure_traineddata_download_pada(tmp_path, monkeypatch):
    from atlas.ops import preflight, winpath
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"x")
    (tmp_path / "tessdata").mkdir()
    monkeypatch.setattr(winpath, "find_binary", lambda k: str(exe))
    def _boom(url, timeout=120):
        raise OSError("mreža pala")
    ok = preflight.ensure_traineddata(("hrv",), out=lambda *_: None, urlopen=_boom)
    assert ok is False


def test_ollama_pull_injektirani_out_svakih_10_posto(monkeypatch):
    """Injektirani out: redak svakih 10 % (ne 100 redaka spama — E2E nalaz)."""
    from atlas.ops import preflight
    import io, json
    events = [json.dumps({"status": "pulling", "total": 100, "completed": i}).encode() + b"\n"
              for i in range(1, 101)]
    events.append(json.dumps({"status": "success"}).encode() + b"\n")
    body = io.BytesIO(b"".join(events))
    body.__enter__ = lambda *a: body
    body.__exit__ = lambda *a: False
    monkeypatch.setattr(preflight.urllib.request, "urlopen",
                        lambda req, timeout=30: body)
    lines = []
    assert preflight.ollama_pull("m", out=lines.append) is True
    pct_lines = [l for l in lines if "%" in l]
    assert 9 <= len(pct_lines) <= 12   # ~svakih 10 %, ne 100 redaka
```

- [ ] **Step 2: run — FAIL**; postojeće ollama_pull/install_via_winget testove pročitaj i prilagodi novom ponašanju (broj redaka postotka; poruke)

- [ ] **Step 3: implementacija u `atlas/ops/preflight.py`**

Importi na vrhu: `from atlas.core.subproc import run_isolated, run_streaming`
(run_isolated je već importan — proširi taj import) i `from atlas.ops import winpath`.

Novi katalog i pomoćne:

```python
_WINGET_INFO = {"ollama": "~700 MB, tipično nekoliko minuta",
                "tesseract": "~60 MB, ispod minute"}
_TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/{lang}.traineddata"


def _dir_writable(d) -> bool:
    """Stvarna proba upisa (os.access je nepouzdan na Windows ACL)."""
    import pathlib
    d = pathlib.Path(d)
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".atlas_write_probe"
        probe.write_bytes(b"x")
        probe.unlink()
        return True
    except OSError:
        return False


def ensure_traineddata(langs=("hrv", "eng"), *, out=print,
                       urlopen=urllib.request.urlopen) -> bool:
    """Osiguraj Tesseract jezike (E2E: UB-Mannheim paket nema hrv).
    Cilj: tessdata uz exe; bez dozvole (Program Files bez elevacije) →
    <data_dir>/tessdata + TESSDATA_PREFIX (proces + trajno). TESSDATA_PREFIX
    je ISKLJUČIV — u fallback mapu se kopiraju i jezici koje primarna već
    ima, inače bi eng nestao."""
    from pathlib import Path
    exe = winpath.find_binary("tesseract")
    if not exe:
        return False
    primary = Path(exe).parent / "tessdata"
    target = primary if _dir_writable(primary) else Path(config.default_data_dir()) / "tessdata"
    fallback_mode = target != primary
    if fallback_mode and not _dir_writable(target):
        out("  ✗ nijedna tessdata lokacija nije upisiva.")
        return False
    for lang in langs:
        fname = f"{lang}.traineddata"
        if (target / fname).exists():
            continue
        if fallback_mode and (primary / fname).exists():
            shutil.copy2(primary / fname, target / fname)
            continue
        out(f"  Skidam {fname} (~15 MB)...")
        try:
            with urlopen(_TESSDATA_URL.format(lang=lang), timeout=120) as r:
                data = r.read()
            (target / fname).write_bytes(data)
        except Exception as e:
            out(f"  ✗ {fname}: {e}")
            return False
        out(f"  ✓ {fname} → {target}")
    if fallback_mode:
        os.environ["TESSDATA_PREFIX"] = str(target)
        winpath.persist_user_env("TESSDATA_PREFIX", str(target))
        out(f"  TESSDATA_PREFIX postavljen: {target}")
    return True
```

`install_via_winget` — novo tijelo (potpis isti):

```python
def install_via_winget(key: str, *, out=print) -> bool:
    """Windows auto-install preko winget allowliste. 'Već instalirano' se
    prepozna i preskoči (E2E nalaz); izlaz ide UŽIVO (run_streaming);
    nakon installa PATH refresh iz registryja + poznate lokacije umjesto
    'restartaj terminal'; tesseract dobiva i hrv/eng jezike."""
    if key not in WINGET_IDS:
        raise ValueError(f"nepoznat paket: {key!r}")
    wid = WINGET_IDS[key]
    cmd = ["winget", "install", "--exact", "--id", wid, "--source", "winget",
           "--accept-package-agreements", "--accept-source-agreements"]
    if platform.system() != "Windows":
        out(f"Auto-install je Windows-only. Ručno: {' '.join(cmd)}")
        out("  (Linux: apt/dnf; macOS: brew — potraži paket u svom package manageru.)")
        return False
    existing = winpath.find_binary(_WINGET_BIN[key])
    if existing:
        out(f"✓ {wid} je već instaliran ({existing}) — preskačem winget.")
    else:
        info = _WINGET_INFO.get(key, "")
        out(f"Instaliram {wid} ({info}) — očekuj UAC potvrdu (klikni Da); "
            "napredak ispod:")
        rc = run_streaming(cmd, timeout=900, out=out)
        if rc != 0:
            out(f"winget nije uspio (rc {rc}).")
            return False
        winpath.refresh_path_from_registry()
    exe = winpath.find_binary(_WINGET_BIN[key])
    if not exe:
        out(f"Instalirano, ali '{_WINGET_BIN[key]}' nije pronađen ni na "
            "poznatim lokacijama — provjeri instalaciju pa ponovi.")
        return False
    if not shutil.which(_WINGET_BIN[key]):
        exe_dir = os.path.dirname(exe)
        winpath.append_user_path(exe_dir)
        out(f"  PATH dopunjen: {exe_dir}")
    if key == "tesseract" and not ensure_traineddata(("hrv", "eng"), out=out):
        return False
    out(f"✓ {wid} spreman ({exe}).")
    return True
```

`requirements()` tesseract blok: `tess = shutil.which("tesseract")` →
`tess = winpath.find_binary("tesseract")`, a `run_isolated(["tesseract", ...])`
→ `run_isolated([tess, "--list-langs"], timeout=5)`.

`ollama_pull` — postotak blok zamijeni:

```python
    stream = out is print and sys.stdout.isatty()
    step = 1 if stream else 10
    ...
                if total and done is not None:
                    pct = int(done * 100 / total)
                    if pct != last_pct and (pct % step == 0 or pct == 100):
                        line = f"  {status}: {pct}%"
                        if stream:
                            sys.stdout.write("\r" + line + "   ")
                            sys.stdout.flush()
                        else:
                            out(line)
                        last_pct = pct
                elif status:
                    if stream and last_pct >= 0:
                        sys.stdout.write("\n")
                        last_pct = -1
                    out(f"  {status}")
```

(na `success`/izlazu iz petlje: ako je stream aktivan i zadnji ispis bio
in-place, ispiši `\n` prije povratka — jedan `if stream and last_pct >= 0:
sys.stdout.write("\n")` prije `return True`).

- [ ] **Step 4: run + puni suite** (prilagođeni postojeći testovi zeleni)

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): živi winget izlaz s najavom, već-instalirano, PATH refresh i hrv traineddata"
```

---

### Task 4: folder_picker.py — TUI preglednik mapa

**Files:**
- Create: `atlas/ops/folder_picker.py`
- Test: `tests/test_folder_picker.py`

**Interfaces:**
- Consumes: `tui_curses.radiolist`.
- Produces: `pick_folder(*, input_fn=input, out=print) -> str | None` (None = odustao/preskoči; vraćena putanja NIJE validirana — pozivatelj validira); `_roots() -> list[str]`; `_subdirs(path) -> list[str]`.

- [ ] **Step 1: failing testovi**

`tests/test_folder_picker.py`:

```python
"""folder_picker: browsanje kroz numerirani fallback (bez TTY-ja)."""
import os

from atlas.ops import folder_picker as fp


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def test_roots_posix():
    if os.name == "nt":
        return
    roots = fp._roots()
    assert "/" in roots and os.path.expanduser("~") in roots


def test_subdirs_abecedno_bez_datoteka(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "datoteka.txt").write_text("x")
    assert fp._subdirs(str(tmp_path)) == ["a", "b"]


def test_subdirs_nedostupno_prazno():
    assert fp._subdirs("/nema/takve/mape") == []


def test_pick_folder_odaberi_trenutnu(tmp_path, monkeypatch):
    """Korijen → uđi u podmapu → [✓ ODABERI OVU MAPU]."""
    (tmp_path / "klijenti").mkdir()
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    # početni ekran: 1=root, 2=Mrežna, 3=Ručni upis, 4=Odustani
    # u mapi: 1=[✓ ODABERI], 2=.., 3+=podmape
    got = fp.pick_folder(input_fn=_reader("1", "3", "1"), out=lambda *_: None)
    assert got == os.path.join(str(tmp_path), "klijenti")


def test_pick_folder_dvije_razine_pa_gore(tmp_path, monkeypatch):
    (tmp_path / "a" / "b").mkdir(parents=True)
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    # uđi u a, uđi u b, gore (..), odaberi a
    got = fp.pick_folder(input_fn=_reader("1", "3", "3", "2", "1"),
                         out=lambda *_: None)
    assert got == os.path.join(str(tmp_path), "a")


def test_pick_folder_odustani():
    assert fp.pick_folder(input_fn=_reader("4"), out=lambda *_: None) is None


def test_pick_folder_rucni_upis(monkeypatch, tmp_path):
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    got = fp.pick_folder(input_fn=_reader("3", r"\\nas\ured"), out=lambda *_: None)
    assert got == r"\\nas\ured"


def test_pick_folder_mrezna_lokacija(monkeypatch, tmp_path):
    """Mrežna opcija: upis \\\\server\\share pa browsanje od te točke —
    ovdje share ne postoji pa _subdirs vrati prazno; odaberi je odmah."""
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    got = fp.pick_folder(input_fn=_reader("2", r"\\nas\share", "1"),
                         out=lambda *_: None)
    assert got == r"\\nas\share"


def test_pick_folder_status_u_naslovu(tmp_path, monkeypatch):
    """Naslov browsanja sadrži trenutnu putanju, header legendu tipki."""
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    lines = []
    fp.pick_folder(input_fn=_reader("1", "1"), out=lines.append)
    text = "\n".join(lines)
    assert str(tmp_path) in text
    assert "ODABERI OVU MAPU" in text
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: implementacija `atlas/ops/folder_picker.py`**

```python
"""TUI preglednik mapa za wizard (stranica 5, E2E nalaz: tipkanje UNC
putanja je mučenje). Nad tui_curses.radiolist: curses na TTY-ju, numerirani
fallback u testovima. Vraćena putanja NIJE validirana — pozivatelj
(page_mape) zadržava svoje UNC/isdir provjere."""
import os
import string

from atlas.ops import tui_curses

_ODABERI = "[✓ ODABERI OVU MAPU]"
_LEGENDA = "Enter = uđi u mapu · prvi red = odaberi OVU mapu · ESC/.. = gore"


def _roots() -> list[str]:
    """Windows: postojeći diskovi A:-Z:; POSIX: ~ i /."""
    if os.name == "nt":
        return [f"{d}:\\" for d in string.ascii_uppercase
                if os.path.exists(f"{d}:\\")]
    return [os.path.expanduser("~"), "/"]


def _subdirs(path: str) -> list[str]:
    """Podmape abecedno; nedostupne lokacije = prazno (bez pada)."""
    def _dir(e):
        try:
            return e.is_dir(follow_symlinks=False)
        except OSError:
            return False
    try:
        with os.scandir(path) as it:
            return sorted(e.name for e in it if _dir(e))
    except OSError:
        return []


def _browse(start: str, *, input_fn, out) -> str | None:
    cur = start
    while True:
        items = [_ODABERI, ".."] + _subdirs(cur)
        sel = tui_curses.radiolist(f"Mapa: {cur}", items, selected=0,
                                   header=_LEGENDA, cancel_returns=1,
                                   input_fn=input_fn, out=out)
        if sel == 0:
            return cur
        if sel == 1:
            parent = os.path.dirname(cur.rstrip("\\/"))
            if not parent or parent == cur:
                return None   # s vrha: izlaz (pozivatelj nudi ponovni ulaz)
            cur = parent
            continue
        cur = os.path.join(cur, items[sel])


def pick_folder(*, input_fn=input, out=print) -> str | None:
    """Početni ekran (diskovi/korijeni + mrežna lokacija + ručni upis) pa
    browsanje. None = odustao."""
    roots = _roots()
    items = (roots
             + ["Mrežna lokacija (\\\\server\\share — upiši jednom, pa browsaj)",
                "Ručni upis putanje (napredno)", "Odustani / preskoči"])
    idx = tui_curses.radiolist("Odaberi polazište:", items, selected=0,
                               cancel_returns=len(items) - 1,
                               input_fn=input_fn, out=out)
    if idx is None or idx == len(items) - 1:
        return None
    if idx == len(roots):       # mrežna lokacija
        try:
            share = input_fn("\\\\server\\share: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return _browse(share, input_fn=input_fn, out=out) if share else None
    if idx == len(roots) + 1:   # ručni upis
        try:
            return input_fn("Putanja: ").strip() or None
        except (EOFError, KeyboardInterrupt):
            return None
    return _browse(roots[idx], input_fn=input_fn, out=out)
```

- [ ] **Step 4: run + puni suite**

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/folder_picker.py tests/test_folder_picker.py
git commit -m "feat(wizard): folder picker — browsanje diskova/mreže umjesto tipkanja putanja"
```

---

### Task 5: page_mape integracija + DRIVE_REMOTE upozorenje

**Files:**
- Modify: `atlas/ops/wizard.py` (page_mape, novi `_drive_warn`; import folder_picker)
- Modify: `tests/test_wizard.py` (page_mape testovi na picker tok)

**Interfaces:**
- Consumes: `folder_picker.pick_folder` (Task 4).
- Produces: `wizard._drive_warn(path) -> bool` — True SAMO za mapirani mrežni pogon (Windows GetDriveTypeW == 4); potpis page_mape nepromijenjen.

- [ ] **Step 1: prilagodi/napiši testove**

U `tests/test_wizard.py` — SVI page_mape testovi sada moraju monkeypatchati
`wizard.folder_picker._roots` na `lambda: ["/fixroot"]` da indeksi budu
deterministički na svim OS-ima (početni ekran: 1=/fixroot, 2=Mrežna,
3=Ručni upis, 4=Odustani):

```python
def _picker_roots(monkeypatch):
    from atlas.ops import folder_picker
    monkeypatch.setattr(folder_picker, "_roots", lambda: ["/fixroot"])


def test_page_mape_preskok(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    ok = wizard.page_mape(s, None, input_fn=_reader("n"), out=lambda *_: None)
    assert ok is True
    from atlas.business import folders
    assert folders.list_folders(s) == []


def test_page_mape_registrira_mapu(tmp_path, monkeypatch):
    _picker_roots(monkeypatch)
    s = init_spine(str(tmp_path / "t.db"))
    d = tmp_path / "klijenti"
    d.mkdir()
    # "d" (poveži); Klijenti: 3=Ručni upis pa putanja; ostale 3 uloge: 4=Odustani
    lines = []
    ok = wizard.page_mape(s, None,
                          input_fn=_reader("d", "3", str(d), "4", "4", "4"),
                          out=lines.append)
    assert ok is True
    from atlas.business import folders
    rows = folders.list_folders(s)
    assert len(rows) == 1 and rows[0]["role"] == "klijenti"
    assert any("ATLAS_MOUNT_ROOTS" in l for l in lines)


def test_page_mape_nedostupna_pa_odustane(tmp_path, monkeypatch):
    _picker_roots(monkeypatch)
    s = init_spine(str(tmp_path / "t.db"))
    bad_unc = r"\\nas\share\nema"
    # "d"; Klijenti: 3=Ručni upis, nepostojeći UNC, "n" (bez retrya); ostale: 4
    lines = []
    ok = wizard.page_mape(s, None,
                          input_fn=_reader("d", "3", bad_unc, "n", "4", "4", "4"),
                          out=lines.append)
    assert ok is True
    assert any("net use" in l for l in lines)
    from atlas.business import folders
    assert folders.list_folders(s) == []


def test_page_mape_ne_unc_putanja_nema_net_use(tmp_path, monkeypatch):
    _picker_roots(monkeypatch)
    s = init_spine(str(tmp_path / "t.db"))
    lines = []
    ok = wizard.page_mape(s, None,
                          input_fn=_reader("d", "3", "/nema/takve/mape", "n",
                                           "4", "4", "4"),
                          out=lines.append)
    assert ok is True
    text = "\n".join(lines)
    assert "nije UNC" in text
    assert "net use" not in text.lower()


def test_page_mape_drive_warn_samo_za_remote(tmp_path, monkeypatch):
    """E2E nalaz: lokalni fiksni disk (D:\\KLIJENTI) NE dobiva ⚠; upozorenje
    samo kad _drive_warn kaže DRIVE_REMOTE."""
    _picker_roots(monkeypatch)
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "_drive_warn", lambda p: True)
    lines = []
    wizard.page_mape(s, None,
                     input_fn=_reader("d", "3", "Z:\\skenovi", "n", "4", "4", "4"),
                     out=lines.append)
    assert any("mrežni pogon" in l for l in lines)


def test_page_mape_lokalni_disk_bez_upozorenja(tmp_path, monkeypatch):
    _picker_roots(monkeypatch)
    s = init_spine(str(tmp_path / "t.db"))
    d = tmp_path / "kl"
    d.mkdir()
    monkeypatch.setattr(wizard, "_drive_warn", lambda p: False)
    lines = []
    wizard.page_mape(s, None, input_fn=_reader("d", "3", str(d), "4", "4", "4"),
                     out=lines.append)
    assert not any("mrežni pogon" in l for l in lines)


def test_drive_warn_ne_windows_uvijek_false():
    if wizard.os.name == "nt":
        return
    assert wizard._drive_warn("Z:\\bilo") is False
    assert wizard._drive_warn("/posix/putanja") is False
```

OBRIŠI stari `test_page_mape_upozorava_na_slovo_pogona` (staro ponašanje —
paušalno upozorenje za svako slovo pogona — namjerno ukinuto, E2E nalaz).

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: implementacija u `atlas/ops/wizard.py`**

Import: proširi `from atlas.ops import ...` s `folder_picker`.

Novi `_drive_warn` (uz `_net_use_hint`):

```python
def _drive_warn(path: str) -> bool:
    """⚠ samo za MAPIRANI MREŽNI pogon (DRIVE_REMOTE=4) — lokalni fiksni
    disk servis normalno vidi (E2E: D:\\KLIJENTI lažno upozorenje)."""
    m = re.match(r"^([A-Za-z]:)", path)
    if not m or os.name != "nt":
        return False
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(m.group(1) + "\\") == 4
    except Exception:
        return False
```

U `page_mape` unutarnjoj petlji zamijeni unos putanje i upozorenje:

```python
        while True:
            out(f"{naziv}:")
            path = folder_picker.pick_folder(input_fn=input_fn, out=out)
            if not path:
                break
            if _drive_warn(path):
                out("  ⚠ Mapirani mrežni pogon (npr. Z:) servisni račun ne vidi "
                    "— koristi UNC putanju (\\\\server\\share\\...).")
            if not os.path.isdir(path):
                ... (POSTOJEĆI blok: ✗ Nedostupno, net use hint / nije UNC,
                     "Pokušaj ponovno?" — NE mijenjaj)
```

(stari `re.match(r"^[A-Za-z]:", path)` blok s bezuvjetnim ⚠ se briše —
zamijenjen `_drive_warn` pozivom).

- [ ] **Step 4: run + puni suite** (`python -m pytest tests/test_wizard.py tests/test_folder_picker.py -q` pa `python -m pytest -q`)

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/wizard.py tests/test_wizard.py
git commit -m "feat(wizard): stranica 5 folder picker + upozorenje samo za mapirani mrežni pogon"
```

---

### Task 6: install.ps1 uskladba s wizardom

**Files:**
- Modify: `install.ps1`

**Interfaces:**
- Consumes: `atlas setup` wizard (postoji).

- [ ] **Step 1: izmjene** (PAZI: datoteka MORA zadržati UTF-8 BOM na početku
i CRLF završetke — PS 5.1 bez BOM-a čita ANSI i puca na ✓/— znakovima,
E2E nalaz; uređuj samo sadržaj, ne encoding):

1. Obriši CIJELU sekciju `# --- 5. operater (owner) ---` (redci s
   `$owner`... do kraja elseif bloka) — wizard kreira operatera
   (stranica 2), install.ps1 ne smije pitati (E2E: zbunjuje i preko
   setup_complete migracije preskoči wizard).
2. Obriši headless seed poziv (redci `$eap = ...; & $atlas setup 2>$null |
   Out-Null; $ErrorActionPreference = $eap` u sekciji 4) — `atlas setup`
   wizard sam sjedi bazu; headless poziv s Out-Null je upravo migracijska
   zamka iz 1.
3. Sekciju `# --- 6. gotovo ---` zamijeni (makni $port/login URL — wizard
   sam kaže završni URL s HTTPS 8443):

```powershell
# --- 5. gotovo ---
$dataDir = if ($env:ATLAS_DATA_DIR) { $env:ATLAS_DATA_DIR } else { Join-Path $env:USERPROFILE ".atlas" }
Write-Host ""
Write-Host "════════════════════════════════════════════"
Write-Host "✓ Okolina spremna.  Podaci: $dataDir"
Write-Host ""
Write-Host "Dovrši postavljanje čarobnjakom (preduvjeti, operater, model, HTTPS, mape):"
Write-Host "  .\.venv\Scripts\atlas.exe setup"
Write-Host ""
Write-Host "Provjera:   .\.venv\Scripts\atlas.exe doctor"
Write-Host "Deploy:     docs\DEPLOY_URED.md (KLIJENTI mapa, uredaji, HTTPS, GDPR)"
Write-Host "════════════════════════════════════════════"
```

4. Renumeriraj komentare sekcija (4 → embedding ostaje, stara 5 nestaje).

- [ ] **Step 2: provjera** — `head -c 3 install.ps1 | xxd` mora pokazati
`ef bb bf` (BOM); `grep -n "auth add\|Read-Host\|atlas setup 2" install.ps1`
→ bez pogodaka (osim možda komentara koji kaže da wizard kreira operatera);
`file install.ps1` i `git diff` vizualno.

- [ ] **Step 3: puni suite** (`python -m pytest -q` — install.ps1 nema
testova, suite potvrđuje da ništa drugo nije dirano)

- [ ] **Step 4: Commit**

```bash
git add install.ps1
git commit -m "fix(install): install.ps1 samo priprema okolinu — operater i seed idu kroz atlas setup wizard"
```
