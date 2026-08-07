# TUI face-lift 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prečac na radnoj površini (sve platforme), pull s kvant sufiksom + usporedba veličine, kozmetika (kose crte, warnings, bge-m3 feature-detect).

**Architecture:** Novi samostalni modul `atlas/ops/shortcut.py` (platformske grane, injektabilan subprocess); kvant logika kao čiste funkcije u preflightu + petlja u page_model; kozmetika = 4 kirurške izmjene (config/preflight/embed/wizard).

**Tech Stack:** Python 3.11+ stdlib, pytest. Bez novih ovisnosti.

## Global Constraints

- Hrvatski latinica s dijakriticima; NIKAD ćirilica.
- Bez novih ovisnosti; testovi bez mreže/stdina/pravih subprocessa/TTY-ja.
- Puni suite u prvom planu prije svakog commita (`python -m pytest -q`, prije grane: 1217 passed, 1 skipped).
- Hrvatske konvencionalne commit poruke + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Potpisi wizard stranica/launch_now se NE mijenjaju.

---

### Task 1: shortcut.py — prečac na radnoj površini + launch_now integracija

**Files:**
- Create: `atlas/ops/shortcut.py`
- Modify: `atlas/ops/wizard.py` (launch_now: poziv nakon što je url poznat; import shortcut)
- Test: `tests/test_shortcut.py`; dopuna `tests/test_wizard.py` (launch_now testovi — shortcut se mocka)

**Interfaces:**
- Produces: `shortcut.create_desktop_shortcut(url, *, name="ATLAS", out=print, run=None, system=platform.system) -> bool` (`run` = injektabilan callable(cmd: list) -> int za PowerShell granu; None = subprocess.call).
- `shortcut._desktop_dir() -> str | None`; `shortcut._browser_exe() -> str | None` (Windows poznate lokacije msedge/chrome).

- [ ] **Step 1: failing testovi**

`tests/test_shortcut.py`:

```python
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
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: implementacija `atlas/ops/shortcut.py`**

```python
"""Prečac na radnoj površini (E2E: korisnik zatvorio app-prozor i nije se
znao vratiti). Windows .lnk (app-prozor) s .url fallbackom, Linux .desktop,
macOS .webloc. Subprocess injektabilan — testovi bez pravih procesa."""
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
    """Puni put do Edge/Chrome za --app prozor (nisu na PATH-u)."""
    for p in _BROWSERS:
        if os.path.isfile(p):
            return p
    return None


def _desktop_dir() -> str | None:
    """Radna površina: XDG_DESKTOP_DIR → USERPROFILE\\Desktop → ~/Desktop.
    None kad ne postoji (OneDrive preusmjerenja i sl. — bolje preskočiti
    nego pogađati)."""
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
    """Napravi prečac na radnoj površini. True = napravljen (bilo koja
    varijanta), False = nema radne površine ili upis pao."""
    if run is None:
        run = lambda cmd: subprocess.call(  # pragma: no cover - tanki omot
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
    """`.lnk` s app-prozorom (WScript.Shell preko PowerShella); browser
    nenađen ili PS pao → `.url` (zadani preglednik). Start Menu kopija uz
    .lnk — korisnik ga nađe i preko Start pretrage."""
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
```

- [ ] **Step 4: launch_now integracija** — u `atlas/ops/wizard.py`, odmah
NAKON `url = f"https://{url_host}:{port}"` (prije pitanja o startu):

```python
    from atlas.ops import shortcut
    shortcut.create_desktop_shortcut(url, out=out)
```

Postojeći launch_now testovi u tests/test_wizard.py: dodaj svakome
`monkeypatch.setattr(wizard.shortcut, "create_desktop_shortcut", lambda url, **k: True)`
— ILI (bolje, jedan potez) modul-level import u wizard.py
(`from atlas.ops import ..., shortcut` u postojećem import retku) pa
monkeypatch na `wizard.shortcut`. Dodaj i jedan novi test:

```python
def test_launch_now_stvara_precac(tmp_path, monkeypatch):
    """Prečac se stvara i kad korisnik NE pokrene server."""
    s = init_spine(str(tmp_path / "t.db"))
    made = []
    monkeypatch.setattr(wizard.shortcut, "create_desktop_shortcut",
                        lambda url, **k: made.append(url) or True)
    wizard.launch_now(s, None, input_fn=_reader("n"), out=lambda *_: None,
                      popen=lambda *a, **k: None)
    assert made and made[0].startswith("https://")
```

- [ ] **Step 5: run + puni suite**

- [ ] **Step 6: Commit**

```bash
git add atlas/ops/shortcut.py atlas/ops/wizard.py tests/test_shortcut.py tests/test_wizard.py
git commit -m "feat(wizard): prečac na radnoj površini — .lnk app-prozor/.url, .desktop, .webloc"
```

---

### Task 2: pull s kvant sufiksom + usporedba veličine

**Files:**
- Modify: `atlas/ops/preflight.py` (quant_tags, ollama_model_size)
- Modify: `atlas/ops/wizard.py` (page_model pull petlja + spremanje stvarnog taga + usporedba veličine)
- Test: `tests/test_preflight.py`, `tests/test_wizard.py`

**Interfaces:**
- Produces: `preflight.quant_tags(ollama_name: str, quant: str) -> list[str]`; `preflight.ollama_model_size(tag: str, url: str) -> float` (GB, 0.0 na grešku/nepoznato).
- Consumes: `model_table.disk_gb` (procjena za usporedbu).

- [ ] **Step 1: failing testovi**

U `tests/test_preflight.py`:

```python
def test_quant_tags_kandidati():
    from atlas.ops import preflight
    assert preflight.quant_tags("qwen2.5:7b", "Q4_K_M") == [
        "qwen2.5:7b-instruct-q4_K_M", "qwen2.5:7b-q4_K_M"]


def test_quant_tags_prazno_bez_kvanta_ili_taga():
    from atlas.ops import preflight
    assert preflight.quant_tags("qwen2.5:7b", "") == []
    assert preflight.quant_tags("bezdvotocke", "Q4_K_M") == []


def test_ollama_model_size_iz_api_tags(monkeypatch):
    from atlas.ops import preflight
    import io, json
    body = json.dumps({"models": [
        {"name": "qwen2.5:7b-instruct-q4_K_M", "size": 4_700_000_000},
        {"name": "phi3:mini", "size": 2_200_000_000}]}).encode()

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(preflight.urllib.request, "urlopen",
                        lambda url, timeout=10: _R(body))
    assert abs(preflight.ollama_model_size("phi3:mini", "http://x") - 2.2) < 0.1
    assert preflight.ollama_model_size("nema:tag", "http://x") == 0.0


def test_ollama_model_size_greska_nula(monkeypatch):
    from atlas.ops import preflight
    def _boom(url, timeout=10):
        raise OSError("dolje")
    monkeypatch.setattr(preflight.urllib.request, "urlopen", _boom)
    assert preflight.ollama_model_size("x:y", "http://x") == 0.0
```

U `tests/test_wizard.py` (uz postojeće page_model testove — pull mock
sada mora primati RAZNE tagove; prilagodi postojeće mockove da prihvate
svaki tag i vrate True na prvi poziv gdje treba):

```python
def test_page_model_pull_kvant_sufiks_prvi(tmp_path, monkeypatch):
    """Prvi kandidat s kvant sufiksom uspije → sprema se TAJ tag."""
    s = init_spine(str(tmp_path / "t.db"))
    _model_mocks(monkeypatch)   # pomoćni: ollama_ready/version/floor/system_state/llmfit_models (v. dolje)
    pulled = []
    monkeypatch.setattr(wizard.preflight, "ollama_pull",
                        lambda m, url, out=print: pulled.append(m) or True)
    monkeypatch.setattr(wizard.preflight, "ollama_model_size",
                        lambda tag, url: 3.9)
    monkeypatch.setattr(wizard, "setup_embedding", lambda s_, c, out=print: "emb")
    monkeypatch.setattr(wizard, "self_test",
                        lambda s_, c, input_fn=input, out=print: True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "x"
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader("1"), out=lambda *_: None)
    assert ok is True
    assert pulled == ["qwen2.5:7b-instruct-q4_K_M"]
    assert s.get_override("model", "model") == "qwen2.5:7b-instruct-q4_K_M"


def test_page_model_pull_fallback_na_goli_tag(tmp_path, monkeypatch):
    """Kandidati s kvantom ne postoje → goli tag + ⚠ upozorenje."""
    s = init_spine(str(tmp_path / "t.db"))
    _model_mocks(monkeypatch)
    pulled = []

    def _pull(m, url, out=print):
        pulled.append(m)
        return m == "qwen2.5:7b"   # samo goli tag uspije
    monkeypatch.setattr(wizard.preflight, "ollama_pull", _pull)
    monkeypatch.setattr(wizard.preflight, "ollama_model_size",
                        lambda tag, url: 4.7)
    monkeypatch.setattr(wizard, "setup_embedding", lambda s_, c, out=print: "emb")
    monkeypatch.setattr(wizard, "self_test",
                        lambda s_, c, input_fn=input, out=print: True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "x"
    lines = []
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader("1"), out=lines.append)
    assert ok is True
    assert pulled == ["qwen2.5:7b-instruct-q4_K_M", "qwen2.5:7b-q4_K_M",
                      "qwen2.5:7b"]
    assert s.get_override("model", "model") == "qwen2.5:7b"
    text = "\n".join(lines)
    assert "registry nema izračunati kvant" in text.lower() or "zadani tag" in text


def test_page_model_upozorenje_kad_stvarno_vece(tmp_path, monkeypatch):
    """Stvarna veličina > 1.3 × procjene → ⚠ redak s obje brojke."""
    s = init_spine(str(tmp_path / "t.db"))
    _model_mocks(monkeypatch)
    monkeypatch.setattr(wizard.preflight, "ollama_pull",
                        lambda m, url, out=print: True)
    monkeypatch.setattr(wizard.preflight, "ollama_model_size",
                        lambda tag, url: 9.9)   # procjena ~4.3 GB → 2.3×
    monkeypatch.setattr(wizard, "setup_embedding", lambda s_, c, out=print: "emb")
    monkeypatch.setattr(wizard, "self_test",
                        lambda s_, c, input_fn=input, out=print: True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "x"
    lines = []
    wizard.page_model(s, _Cfg(), input_fn=_reader("1"), out=lines.append)
    assert any("9.9" in l.replace(",", ".") and "⚠" in l for l in lines)
```

`_model_mocks` pomoćni (dodaj u test_wizard.py iznad novih testova):

```python
def _model_mocks(monkeypatch):
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url: (True, "ok"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url: "0.5.0")
    monkeypatch.setattr(wizard.preflight, "ollama_floor_ok", lambda v: True)
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ram_total_gb": 8.0, "ram_free_gb": 5.5,
                                        "disk_free_gb": 90.0})
    rows = [{"ollama_name": "qwen2.5:7b", "params": "7B", "best_quant": "Q4_K_M",
             "memory_gb": 5.2, "tps": 11.0, "fit_label": "Good", "use_case": ""}]
    monkeypatch.setattr(wizard.preflight, "llmfit_models", lambda cfg: rows)
```

Postojeće
page_model testove uskladi: pull mock koji je asertirao točno
`["qwen2.5:7b"]` sada očekuje prvi kandidat s kvantom (ili mockaj
`quant_tags` na `lambda n, q: []` gdje test ne cilja kvant logiku).

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: implementacija**

`atlas/ops/preflight.py`:

```python
def quant_tags(ollama_name: str, quant: str) -> list[str]:
    """Kandidat-tagovi s TOČNOM kvantizacijom (E2E BUG: goli tag vuče
    registry default — tipično veći od llmfit procjene). Konvencije
    variraju pa lista: '<ime>-instruct-<q>' pa '<ime>-<q>'; pozivatelj
    doda goli tag kao zadnji fallback."""
    if not quant or ":" not in ollama_name:
        return []
    q = quant[0].lower() + quant[1:]   # Q4_K_M → q4_K_M (ollama stil)
    return [f"{ollama_name}-instruct-{q}", f"{ollama_name}-{q}"]


def ollama_model_size(tag: str, url: str = "http://127.0.0.1:11434") -> float:
    """Stvarna veličina skinutog modela u GB (GET /api/tags); 0.0 =
    nepoznato/greška — pozivatelj tada preskače usporedbu."""
    import json
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=10) as r:
            data = json.loads(r.read())
        for m in data.get("models", []):
            if m.get("name") == tag:
                return round(float(m.get("size", 0)) / 1e9, 1)
    except Exception:
        pass
    return 0.0
```

`atlas/ops/wizard.py` — u `page_model` zamijeni pull blok
(`out(f"Skidam {model}...")` do `model_settings.save(...)`):

```python
    row = rows[idx]
    kandidati = preflight.quant_tags(model, row.get("best_quant", "")) + [model]
    pulled_tag = None
    for tag in kandidati:
        if tag != model:
            out(f"Skidam {tag} (točan kvant; prekid je siguran — nastavlja gdje je stalo)...")
        else:
            out(f"Skidam {model} (prekid je siguran — nastavlja gdje je stalo)...")
        if preflight.ollama_pull(tag, url, out=out):
            pulled_tag = tag
            break
    if not pulled_tag:
        out("Model nije skinut. Pokreni setup ponovno ili postavi kasnije u Postavkama.")
        return tui.prompt_yes_no("Nastavi setup bez modela?", default=True,
                                 input_fn=input_fn, out=out)
    if pulled_tag == model and len(kandidati) > 1:
        out("  ⚠ Registry nema izračunati kvant — skinut zadani tag "
            "(može biti veći od procjene).")
    stvarno = preflight.ollama_model_size(pulled_tag, url)
    if stvarno:
        procjena = model_table.disk_gb(row.get("params", ""), row.get("best_quant", ""))
        linija = f"  Stvarna veličina: {stvarno:.1f} GB"
        if procjena:
            linija += f" (procjena {procjena:.1f} GB)"
        if procjena and stvarno > procjena * 1.3:
            linija = "  ⚠" + linija.removeprefix("  ") + " — veće od procjene!"
        out(linija)

    emb = setup_embedding(spine, cfg, out=out)
    from atlas.business import model_settings
    model_settings.save(spine, "ollama", model=pulled_tag, ollama_url=url,
                        embed_model=emb or "", user="setup")
```

- [ ] **Step 4: run + puni suite**

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/preflight.py atlas/ops/wizard.py tests/test_preflight.py tests/test_wizard.py
git commit -m "fix(wizard): pull s točnim kvant tagom + usporedba stvarne veličine s procjenom"
```

---

### Task 3: kozmetika — normpath, warnings, bge-m3 feature-detect

**Files:**
- Modify: `atlas/config.py` (normpath na data_dir)
- Modify: `atlas/ops/preflight.py` (requirements optional-moduli bez warning curenja)
- Modify: `atlas/rag/embed.py` (download_model: HF/fastembed warnings; novi supports())
- Modify: `atlas/ops/wizard.py` (choose_embed_model: supports guard)
- Test: `tests/test_config_compat.py` ili `tests/test_config.py` (normpath), `tests/test_embed_download.py` (supports), `tests/test_wizard.py` (choose_embed_model)

**Interfaces:**
- Produces: `embed.supports(model_name: str) -> bool` — robusno False na bilo koju grešku.

- [ ] **Step 1: failing testovi**

`tests/test_config.py` (dodaj):

```python
def test_data_dir_normpath_bez_mijesanih_crta(monkeypatch, tmp_path):
    """E2E kozmetika: 'C:\\Users\\X/.atlas' — normpath izravnava separatore."""
    for k in ("ATLAS_DATA_DIR", "RAGSPINE_DATA_DIR"):  # compat: ragspine
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path) + "/./poddir")
    monkeypatch.delenv("ATLAS_JWT_SECRET", raising=False)
    monkeypatch.delenv("RAGSPINE_JWT_SECRET", raising=False)  # compat: ragspine
    from atlas import config
    cfg = config.Config.from_env()
    assert "/./" not in cfg.data_dir
    assert cfg.data_dir == os.path.normpath(cfg.data_dir)
```

`tests/test_embed_download.py` (dodaj):

```python
def test_supports_pozitivno(monkeypatch):
    from atlas.rag import embed

    class _TE:
        @staticmethod
        def list_supported_models():
            return [{"model": "BAAI/bge-m3"}, {"model": "mali/model"}]
    monkeypatch.setattr(embed, "_text_embedding_cls", lambda: _TE)
    assert embed.supports("BAAI/bge-m3") is True
    assert embed.supports("nema/toga") is False


def test_supports_robustan_na_greske(monkeypatch):
    from atlas.rag import embed
    monkeypatch.setattr(embed, "_text_embedding_cls",
                        lambda: (_ for _ in ()).throw(ImportError("nema fastembed")))
    assert embed.supports("BAAI/bge-m3") is False
```

`tests/test_wizard.py` (choose_embed_model testovi — postojeće prilagodi):

```python
def test_choose_embed_bge_samo_kad_podrzan(monkeypatch):
    monkeypatch.setattr(wizard.preflight, "fit_pill", lambda s, t: "fits")
    monkeypatch.setattr(wizard.embed, "supports", lambda m: True)
    assert wizard.choose_embed_model({"ram_total_gb": 32}, "d") == wizard._BGE_M3
    monkeypatch.setattr(wizard.embed, "supports", lambda m: False)
    assert wizard.choose_embed_model({"ram_total_gb": 32}, "d") == "d"
```

`tests/test_preflight.py` (fitz warning ne curi):

```python
def test_requirements_ne_pusta_warnings(monkeypatch, recwarn):
    """Optional-modul importi (fitz deprecation i sl.) ne smiju curiti u
    izlaz stranice 1."""
    import warnings
    from atlas.ops import preflight

    def _noisy_import(name):
        warnings.warn("fitz is deprecated", DeprecationWarning)
        raise ImportError("nema")
    import importlib
    monkeypatch.setattr(importlib, "import_module", _noisy_import)
    monkeypatch.setattr(preflight, "system_state",
                        lambda c=None: {"python": "3.11", "ram_total_gb": 8,
                                        "ram_free_gb": 4, "disk_free_gb": 50})
    monkeypatch.setattr(preflight, "ollama_ready", lambda url: (True, "ok"))
    monkeypatch.setattr(preflight, "internet_ok", lambda: True)
    monkeypatch.setattr(preflight.winpath, "find_binary", lambda k: None)
    preflight.requirements(None)
    assert len(recwarn) == 0
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: implementacija**

`atlas/config.py` u from_env: `data_dir = os.path.normpath(os.path.expanduser(...))`
(postojeći izraz omotaj normpathom — E2E kozmetika: miješane kose crte).

`atlas/ops/preflight.py` — optional-modul petlja u requirements:

```python
    import warnings
    for mod, naziv, fix in _OPTIONAL_MODULES:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")   # fitz deprecation i sl. ne u izlaz
                importlib.import_module(mod)
            present = True
        except Exception:
            present = False
```

`atlas/rag/embed.py`:

```python
def _text_embedding_cls():
    """Indirekcija radi testabilnosti (fastembed je optional dep)."""
    from fastembed import TextEmbedding
    return TextEmbedding


def supports(model_name: str) -> bool:
    """Podržava li instalirani fastembed model (E2E: bge-m3 u ponudi, a
    TextEmbedding ga ne zna → ne nuditi). False na SVAKU grešku — sigurni
    default je mali model."""
    try:
        models = _text_embedding_cls().list_supported_models()
        return any((m.get("model") if isinstance(m, dict) else str(m)) == model_name
                   for m in models)
    except Exception:
        return False
```

`download_model` — na početku (prije fastembed importa/downloada):

```python
    import warnings
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
```

i omotaj samo download/model-load dio s `with warnings.catch_warnings():
warnings.simplefilter("ignore")` (mean-pooling/symlink šum; greške i dalje
normalno propagiraju u {ok: False}). (`import os` postoji? provjeri vrh
embed.py — dodaj ako ne.)

`atlas/ops/wizard.py`:
- import: `from atlas.rag import embed` na vrh (provjeri da NEMA kružnog
  importa: embed → config, ne → wizard; sigurno).
- `choose_embed_model`:

```python
def choose_embed_model(state: dict, default_model: str) -> str:
    """bge-m3 kad KOMOTNO stane u RAM i kad ga fastembed stvarno podržava
    (E2E: unsupported model ne smije u ponudu); inače mali default."""
    total = state.get("ram_total_gb") or 0.0
    if preflight.fit_pill(_BGE_M3_GB, total) == "fits" and embed.supports(_BGE_M3):
        return _BGE_M3
    return default_model
```

Postojeći testovi choose_embed_model: dodaj im
`monkeypatch.setattr(wizard.embed, "supports", lambda m: True)` da zadrže
staro ponašanje.

- [ ] **Step 4: run + puni suite**

- [ ] **Step 5: Commit**

```bash
git add atlas/config.py atlas/ops/preflight.py atlas/rag/embed.py atlas/ops/wizard.py tests/
git commit -m "fix(kozmetika): normpath data_dir, prigušeni fitz/fastembed warninzi, bge-m3 samo kad je podržan"
```
