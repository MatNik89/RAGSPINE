# Setup Wizard — P1: Temelj + Preduvjeti — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Terminal TUI `ragspine setup` koji provjeri preduvjete i kreira prvog admina, s trajnim resumable stanjem i web-gatekeeperom vezanim na `setup_complete`.

**Architecture:** Novi TUI wizard (`ragspine/ops/wizard.py`) orkestrira sekcije s resume-om preko `config_overrides` tablice (module="setup"). Stranica 1 = prošireni `preflight.requirements()`; stranica 2 = `firstrun.create_first_owner`. Web middleware preusmjerava na wizard dok `setup_complete != true`.

**Tech Stack:** Python 3.11+, SQLite (`config_overrides`), FastAPI middleware, stdlib (`hashlib`, `shutil`, `urllib`), pytest.

## Global Constraints

- Jezik koda/komentara/UI stringova: hrvatski (latinica). Cyrillic-gate `tests/test_no_cyrillic.py` mora ostati zelen.
- Python floor: 3.11+.
- Bez novih dependencija (stdlib only za P1).
- CI zelen na 4 posla (ubuntu 3.11/3.13, macos 3.13, windows 3.11).
- TUI funkcije primaju injektabilni `input_fn`/`out` radi testabilnosti (bez pravog stdina u testovima).
- Setup-stanje živi u `config_overrides(module='setup', key, value)` — NE nova tablica.
- Lozinke: PBKDF2-HMAC-SHA256, **600k** iteracija (novi zapisi), migrabilni format sa starim 200k.
- Gatekeeper gleda `setup_complete`, NE „ima li korisnika".

---

### Task 1: Setup-state helpers (resume temelj)

**Files:**
- Create: `ragspine/ops/wizard_state.py`
- Test: `tests/test_wizard_state.py`

**Interfaces:**
- Consumes: `spine` (ima `.read()`, `.write()` context managere; `config_overrides` tablica postoji u shemi).
- Produces:
  - `get_stage(spine) -> int` (0 ako nema zapisa)
  - `set_stage(spine, stage: int) -> None`
  - `is_complete(spine) -> bool`
  - `mark_complete(spine) -> None`
  - `reset(spine) -> None` (briše module='setup' zapise)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard_state.py
from ragspine.core.spine import init_spine
from ragspine.ops import wizard_state as ws


def _spine(tmp_path):
    return init_spine(str(tmp_path / "t.db"))


def test_stage_defaults_zero(tmp_path):
    s = _spine(tmp_path)
    assert ws.get_stage(s) == 0
    assert ws.is_complete(s) is False


def test_stage_roundtrip_and_complete(tmp_path):
    s = _spine(tmp_path)
    ws.set_stage(s, 2)
    assert ws.get_stage(s) == 2
    ws.set_stage(s, 3)  # upsert, ne duplira
    assert ws.get_stage(s) == 3
    ws.mark_complete(s)
    assert ws.is_complete(s) is True


def test_reset_clears(tmp_path):
    s = _spine(tmp_path)
    ws.set_stage(s, 4)
    ws.mark_complete(s)
    ws.reset(s)
    assert ws.get_stage(s) == 0
    assert ws.is_complete(s) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard_state.py -v`
Expected: FAIL — `ModuleNotFoundError: ragspine.ops.wizard_state`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/wizard_state.py
"""Trajno stanje setup wizarda u config_overrides(module='setup').
Omogućuje resume: pad usred wizarda -> nastavak od zadnjeg dovršenog koraka."""

_MOD = "setup"


def _get(spine, key: str) -> str | None:
    r = spine.read().execute(
        "SELECT value FROM config_overrides WHERE module=? AND key=?", (_MOD, key)
    ).fetchone()
    return r["value"] if r else None


def _put(spine, key: str, value: str) -> None:
    with spine.write() as c:
        c.execute(
            """INSERT INTO config_overrides(module, key, value, updated_at)
               VALUES(?,?,?,datetime('now'))
               ON CONFLICT(module, key) DO UPDATE SET
                 value=excluded.value, updated_at=excluded.updated_at""",
            (_MOD, key, value),
        )


def get_stage(spine) -> int:
    v = _get(spine, "stage")
    try:
        return int(v) if v is not None else 0
    except ValueError:
        return 0


def set_stage(spine, stage: int) -> None:
    _put(spine, "stage", str(int(stage)))


def is_complete(spine) -> bool:
    return _get(spine, "complete") == "true"


def mark_complete(spine) -> None:
    _put(spine, "complete", "true")


def reset(spine) -> None:
    with spine.write() as c:
        c.execute("DELETE FROM config_overrides WHERE module=?", (_MOD,))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard_state.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/wizard_state.py tests/test_wizard_state.py
git commit -m "feat(setup): trajno wizard-stanje (stage/complete) u config_overrides"
```

---

### Task 2: Gatekeeper na `setup_complete` (ne „ima korisnika")

**Files:**
- Modify: `ragspine/web/firstrun.py` (dodaj `needs_setup`, zadrži `needs_onboarding` kao alias)
- Modify: `ragspine/web/api.py:460-463` (middleware koristi `needs_setup`)
- Test: `tests/test_firstrun.py` (dodaj slučaj: admin postoji ali setup nije complete → i dalje redirect)

**Interfaces:**
- Consumes: `wizard_state.is_complete` (Task 1), `firstrun._redirect_target`.
- Produces: `needs_setup(spine) -> bool` (True dok `setup_complete != true`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_firstrun.py  (dodaj)
from ragspine.core.spine import init_spine
from ragspine.web import firstrun
from ragspine.web.deps import add_user
from ragspine.ops import wizard_state as ws


def test_needs_setup_true_even_with_user_until_complete(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    add_user(s, "admin", "lozinka12", role="admin")
    # korisnik postoji, ali setup nije označen gotovim
    assert firstrun.needs_setup(s) is True
    ws.mark_complete(s)
    assert firstrun.needs_setup(s) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_firstrun.py::test_needs_setup_true_even_with_user_until_complete -v`
Expected: FAIL — `AttributeError: module 'ragspine.web.firstrun' has no attribute 'needs_setup'`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/web/firstrun.py  (dodaj ispod needs_onboarding)
def needs_setup(spine) -> bool:
    """Setup nije gotov dok wizard ne postavi setup_complete. Vezano na flag,
    NE na postojanje korisnika (admin se kreira usred wizarda — Codex/hermes)."""
    from ragspine.ops import wizard_state
    return not wizard_state.is_complete(spine)
```

```python
# ragspine/web/api.py:461-462  — zamijeni needs_onboarding sa needs_setup
        if request.method == "GET" and firstrun._redirect_target(request.url.path) \
                and firstrun.needs_setup(spine):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_firstrun.py -v`
Expected: PASS (novi test + postojeći)

- [ ] **Step 5: Run full web-gatekeeper regresija**

Run: `pytest tests/test_firstrun.py tests/test_ui.py -q`
Expected: PASS (ako neki test pretpostavlja „user==setup gotov", ažuriraj ga da zove `ws.mark_complete`)

- [ ] **Step 6: Commit**

```bash
git add ragspine/web/firstrun.py ragspine/web/api.py tests/test_firstrun.py
git commit -m "fix(setup): gatekeeper na setup_complete flag, ne na postojanje korisnika"
```

---

### Task 3: PBKDF2 200k→600k, migrabilni format

**Files:**
- Modify: `ragspine/core/security.py:50-61` (`hash_password`, `verify_password`)
- Test: `tests/test_security.py` (dodaj; ako ne postoji, kreiraj)

**Interfaces:**
- Produces: `hash_password(pw)` koristi 600k i format `pbkdf2$<iter>$<salt>$<hash>`; `verify_password` čita i novi i stari (`<salt>$<hash>` = 200k) format.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_security.py  (dodaj)
import hashlib
from ragspine.core.security import hash_password, verify_password


def test_new_hash_uses_600k_and_verifies():
    h = hash_password("tajna123")
    assert h.startswith("pbkdf2$600000$")
    assert verify_password("tajna123", h) is True
    assert verify_password("krivo", h) is False


def test_verifies_legacy_200k_format():
    # stari format: <salt_hex>$<hash_hex>, 200k
    salt = bytes(range(16))
    d = hashlib.pbkdf2_hmac("sha256", "staro".encode(), salt, 200_000)
    legacy = f"{salt.hex()}${d.hex()}"
    assert verify_password("staro", legacy) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_security.py -v`
Expected: FAIL — novi hash nema prefiks `pbkdf2$600000$`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/core/security.py  — zamijeni hash_password/verify_password
_PBKDF2_ITERS = 600_000


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2${_PBKDF2_ITERS}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        if stored.startswith("pbkdf2$"):
            _, iters_s, salt_hex, hash_hex = stored.split("$")
            iters = int(iters_s)
        else:
            salt_hex, hash_hex = stored.split("$")   # legacy 200k
            iters = 200_000
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), iters)
    except ValueError:
        return False
    return secrets.compare_digest(h.hex(), hash_hex)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_security.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add ragspine/core/security.py tests/test_security.py
git commit -m "feat(security): PBKDF2 600k migrabilni format, čita legacy 200k (OWASP 2026)"
```

---

### Task 4: Preflight — Tesseract obavezan + Ollama health-check

**Files:**
- Modify: `ragspine/ops/preflight.py` (`requirements()` — Tesseract fail-blok, novi red Ollama)
- Test: `tests/test_preflight.py` (dodaj)

**Interfaces:**
- Produces: `requirements()` vraća red `key="tesseract"` sa `status="fail"` (ne `warn`) kad nedostaje/nema jezika; novi red `key="ollama"` sa `status`/`detalj` iz health-checka.
- Nova pomoćna: `ollama_ready(url="http://localhost:11434") -> tuple[bool, str]` (200 na `/api/tags` = radi).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj)
from ragspine.ops import preflight


def test_tesseract_missing_is_fail(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    reqs = {r["key"]: r for r in preflight.requirements()}
    assert reqs["tesseract"]["status"] == "fail"   # bio "warn"


def test_ollama_row_present(monkeypatch):
    monkeypatch.setattr(preflight, "ollama_ready", lambda url=None: (False, "nije dostupna"))
    reqs = {r["key"]: r for r in preflight.requirements()}
    assert "ollama" in reqs
    assert reqs["ollama"]["status"] in ("warn", "fail")
    assert "Ollama" in reqs["ollama"]["naziv"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k "tesseract_missing or ollama_row" -v`
Expected: FAIL — tesseract je `warn`; `ollama_ready`/red ne postoji

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/preflight.py

def ollama_ready(url: str = "http://localhost:11434") -> tuple[bool, str]:
    """200 na /api/tags = servis radi. Ne oslanja se na `ollama --version`
    (hermes: instalirana ali ne startana je čest slučaj)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=3) as r:
            return (r.status == 200, "servis radi")
    except Exception:
        return (False, "nije dostupna (servis ne radi ili nije instalirana)")
```

```python
# ragspine/ops/preflight.py  — u requirements(), Tesseract red: warn -> fail
    out.append({"key": "tesseract", "naziv": "OCR (Tesseract, hrv+eng)",
                "status": _status(bool(tess) and langs_ok),   # bez warn=True -> fail kad nema
                "detalj": tdetail,
                "fix": "winget install UB-Mannheim.TesseractOCR (+ hrv i eng jezični paket)"})

    ok, odetail = ollama_ready(getattr(cfg, "ollama_url", "http://localhost:11434"))
    out.append({"key": "ollama", "naziv": "Ollama (pokretač lokalnog LLM-a)",
                "status": _status(ok, warn=True), "detalj": odetail,
                "fix": "winget install Ollama.Ollama pa pokreni 'ollama serve'"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py -v`
Expected: PASS (uklj. postojeće preflight testove — ako neki očekuje tesseract=warn, ažuriraj na fail)

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): Tesseract obavezan (jedini first-run OCR) + Ollama health-check /api/tags"
```

---

### Task 5: Preflight — internet=status, statička adresa, proxy

**Files:**
- Modify: `ragspine/ops/preflight.py` (`system_state()` — dodaj `ip_mode`; `requirements()` — internet red kao warn, ne fail)
- Test: `tests/test_preflight.py` (dodaj)

**Interfaces:**
- Produces: `system_state()` ključ `ip_mode` ∈ {"static","dhcp","unknown"}; `internet_ok() -> bool`; `requirements()` red `key="internet"` sa `status="warn"` kad nema neta (NE blokira).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj)
from ragspine.ops import preflight


def test_internet_is_warn_not_fail(monkeypatch):
    monkeypatch.setattr(preflight, "internet_ok", lambda: False)
    reqs = {r["key"]: r for r in preflight.requirements()}
    assert reqs["internet"]["status"] == "warn"   # offline ne blokira


def test_system_state_has_ip_mode():
    st = preflight.system_state()
    assert st["ip_mode"] in ("static", "dhcp", "unknown")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k "internet_is_warn or ip_mode" -v`
Expected: FAIL — `internet_ok`/`ip_mode` ne postoje

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/preflight.py

def internet_ok(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    import socket
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


def _ip_mode() -> str:
    """static/dhcp/unknown. Windows: netsh; drugdje unknown (detekcija LAN-specific)."""
    import platform
    from ragspine.core.subproc import run_isolated
    if platform.system() != "Windows":
        return "unknown"
    try:
        rc, out, _ = run_isolated(["netsh", "interface", "ip", "show", "config"], timeout=5)
        low = out.lower()
        if "dhcp enabled:" in low and "dhcp enabled:                         yes" in low:
            return "dhcp"
        if "dhcp enabled" in low:
            return "static"
    except Exception:
        pass
    return "unknown"
```

```python
# ragspine/ops/preflight.py  — u system_state(), dodaj u dict:
        "ip_mode": _ip_mode(),
```

```python
# ragspine/ops/preflight.py  — u requirements(), dodaj internet red (warn):
    out.append({"key": "internet", "naziv": "Internet (za skidanje modela/instalacije)",
                "status": _status(internet_ok(), warn=True),
                "detalj": "dostupan" if internet_ok() else "nema — radi offline s onim što ima",
                "fix": "spoji mrežu ili koristi --offline s ručno skinutim modelima"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): internet=status (ne blok), ip_mode static/dhcp detekcija"
```

---

### Task 6: TUI primitivi (injektabilni I/O)

**Files:**
- Create: `ragspine/ops/tui.py`
- Test: `tests/test_tui.py`

**Interfaces:**
- Produces:
  - `prompt_choice(question, choices, *, default=0, input_fn=input, out=print) -> int`
  - `prompt_yes_no(question, *, default=True, input_fn=input, out=print) -> bool`
  - `prompt_text(question, *, default="", input_fn=input, out=print) -> str`
  - `print_header(title, *, out=print) -> None`
  - `status_glyph(status: str) -> str` (`"ok"→"✓"`, `"warn"→"⚠"`, `"fail"→"✗"`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui.py
from ragspine.ops import tui


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def test_prompt_choice_returns_index():
    assert tui.prompt_choice("Q", ["a", "b", "c"], input_fn=_reader("2"), out=lambda *_: None) == 1


def test_prompt_choice_empty_uses_default():
    assert tui.prompt_choice("Q", ["a", "b"], default=1, input_fn=_reader(""), out=lambda *_: None) == 1


def test_prompt_yes_no():
    assert tui.prompt_yes_no("Q", input_fn=_reader("da"), out=lambda *_: None) is True
    assert tui.prompt_yes_no("Q", input_fn=_reader("ne"), out=lambda *_: None) is False
    assert tui.prompt_yes_no("Q", default=False, input_fn=_reader(""), out=lambda *_: None) is False


def test_status_glyph():
    assert tui.status_glyph("ok") == "✓"
    assert tui.status_glyph("warn") == "⚠"
    assert tui.status_glyph("fail") == "✗"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui.py -v`
Expected: FAIL — `ModuleNotFoundError: ragspine.ops.tui`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/tui.py
"""Minimalni terminal-UI primitivi za setup wizard. I/O je injektabilan
(input_fn/out) radi testabilnosti — bez pravog stdina u testovima.
Uzor: NousResearch/hermes-agent hermes_cli/setup.py (prompt_choice/yes_no)."""

_GLYPH = {"ok": "✓", "warn": "⚠", "fail": "✗"}


def status_glyph(status: str) -> str:
    return _GLYPH.get(status, "?")


def print_header(title: str, *, out=print) -> None:
    out("")
    out(f"── {title} " + "─" * max(0, 50 - len(title)))


def prompt_text(question: str, *, default: str = "", input_fn=input, out=print) -> str:
    suffix = f" [{default}]" if default else ""
    ans = input_fn(f"{question}{suffix}: ").strip()
    return ans or default


def prompt_yes_no(question: str, *, default: bool = True, input_fn=input, out=print) -> bool:
    hint = "[D/n]" if default else "[d/N]"
    ans = input_fn(f"{question} {hint}: ").strip().lower()
    if not ans:
        return default
    return ans in ("d", "da", "y", "yes")


def prompt_choice(question: str, choices: list[str], *, default: int = 0,
                  input_fn=input, out=print) -> int:
    out(question)
    for i, c in enumerate(choices, 1):
        out(f"  {i}. {c}")
    while True:
        ans = input_fn(f"Odaberi [1-{len(choices)}] (default {default + 1}): ").strip()
        if not ans:
            return default
        if ans.isdigit() and 1 <= int(ans) <= len(choices):
            return int(ans) - 1
        out("Neispravan izbor.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tui.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/tui.py tests/test_tui.py
git commit -m "feat(setup): TUI primitivi (prompt_choice/yes_no/text, glyph) injektabilni I/O"
```

---

### Task 7: Wizard driver + Stranica 1 (Preduvjeti)

**Files:**
- Create: `ragspine/ops/wizard.py`
- Modify: `ragspine/__main__.py:112` (`_cmd_setup` zove novi wizard)
- Test: `tests/test_wizard.py`

**Interfaces:**
- Consumes: `wizard_state` (Task 1), `tui` (Task 6), `preflight.requirements` (Task 4/5).
- Produces:
  - `render_preflight(reqs, *, out=print) -> bool` (ispiše redove s glyph+fix; vrati True ako nema `fail`)
  - `page_preduvjeti(spine, cfg, *, input_fn=input, out=print) -> bool` (renderira; ako ima fail, ponudi ponovnu provjeru; vrati True kad nema fail → smije dalje)
  - `run(spine, cfg, *, input_fn=input, out=print) -> None` (glavni driver; resume od `get_stage`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard.py
from ragspine.ops import wizard


def test_render_preflight_blocks_on_fail():
    reqs = [
        {"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""},
        {"key": "disk", "naziv": "Disk", "status": "fail", "detalj": "0 GB", "fix": "oslobodi"},
    ]
    lines = []
    ok = wizard.render_preflight(reqs, out=lines.append)
    assert ok is False
    assert any("✗" in l for l in lines)
    assert any("oslobodi" in l for l in lines)   # fix se prikaže za fail


def test_render_preflight_passes_when_no_fail():
    reqs = [
        {"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""},
        {"key": "internet", "naziv": "Internet", "status": "warn", "detalj": "nema", "fix": "spoji"},
    ]
    assert wizard.render_preflight(reqs, out=lambda *_: None) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -v`
Expected: FAIL — `ModuleNotFoundError: ragspine.ops.wizard`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/wizard.py
"""Terminal setup wizard. Jedan fiksni slijed, resume preko wizard_state.
P1: Stranica 1 (preduvjeti) + Stranica 2 (operater). Ostale stranice u P2-P4."""
from ragspine.ops import preflight, tui, wizard_state


def render_preflight(reqs, *, out=print) -> bool:
    """Ispiši preduvjete s glyph+detalj; fix samo za warn/fail. Vrati True kad nema 'fail'."""
    has_fail = False
    for r in reqs:
        g = tui.status_glyph(r["status"])
        out(f"  {g} {r['naziv']} — {r['detalj']}")
        if r["status"] in ("warn", "fail") and r.get("fix"):
            out(f"      → {r['fix']}")
        if r["status"] == "fail":
            has_fail = True
    return not has_fail


def page_preduvjeti(spine, cfg, *, input_fn=input, out=print) -> bool:
    tui.print_header("1/6  Preduvjeti", out=out)
    while True:
        reqs = preflight.requirements(cfg)
        if render_preflight(reqs, out=out):
            return True
        out("")
        out("Neki obavezni preduvjeti nedostaju (✗). Popravi ih pa ponovi.")
        if not tui.prompt_yes_no("Provjeri ponovno?", default=True, input_fn=input_fn, out=out):
            return False


def run(spine, cfg, *, input_fn=input, out=print) -> None:
    if wizard_state.is_complete(spine):
        out("Setup je već dovršen. Za ponovno: `ragspine setup --reset`.")
        return
    stage = wizard_state.get_stage(spine)
    out(f"RAGSPINE setup (nastavak od koraka {stage + 1}).")
    if stage < 1:
        if not page_preduvjeti(spine, cfg, input_fn=input_fn, out=out):
            out("Setup prekinut na preduvjetima. Pokreni ponovno kad popraviš.")
            return
        wizard_state.set_stage(spine, 1)
    # Stranica 2 (operater) — Task 8 nadograđuje ovdje.
    out("Preduvjeti u redu. (Stranica 2 slijedi u Task 8.)")
```

```python
# ragspine/__main__.py:112  — _cmd_setup zove novi wizard
def _cmd_setup(args) -> int:
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine
    from ragspine.ops import wizard, wizard_state
    cfg = get_config()
    spine = init_spine(cfg.db_path)
    if getattr(args, "reset", False):
        wizard_state.reset(spine)
    wizard.run(spine, cfg)
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify --reset flag registriran**

Provjeri da setup subparser ima `--reset` (u `build_parser`/argparse dijelu `__main__.py`). Ako ne, dodaj:
```python
    p_setup.add_argument("--reset", action="store_true", help="obriši setup-stanje i kreni ispočetka")
```
Run: `python -m ragspine setup --help`
Expected: prikazuje `--reset`

- [ ] **Step 6: Commit**

```bash
git add ragspine/ops/wizard.py ragspine/__main__.py tests/test_wizard.py
git commit -m "feat(setup): wizard driver + stranica 1 (preduvjeti) s resume + --reset"
```

---

### Task 8: Stranica 2 (Operater) + bilježenje stagea

**Files:**
- Modify: `ragspine/ops/wizard.py` (dodaj `page_operater`, veži u `run`)
- Test: `tests/test_wizard.py` (dodaj)

**Interfaces:**
- Consumes: `firstrun.create_first_owner`, `tui`, `wizard_state`.
- Produces: `page_operater(spine, *, input_fn=input, out=print) -> bool` (skupi ime+lozinku×2, validira 8+, kreira admina; vrati True na uspjeh). Po uspjehu `run` postavi stage=2. (Napomena: `setup_complete` se NE postavlja u P1 — to je zadnja stranica u P4.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard.py  (dodaj)
from ragspine.core.spine import init_spine
from ragspine.ops import wizard
from ragspine.core.security import verify_password


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def test_page_operater_creates_admin(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    ok = wizard.page_operater(
        s, input_fn=_reader("matej", "lozinka12", "lozinka12"), out=lambda *_: None)
    assert ok is True
    row = s.read().execute("SELECT username, pw_hash FROM users").fetchone()
    assert row["username"] == "matej"
    assert verify_password("lozinka12", row["pw_hash"]) is True


def test_page_operater_rejects_short_password(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    # prvo prekratka pa mismatch pa ispravna
    ok = wizard.page_operater(
        s, input_fn=_reader("matej", "kratka", "kratka", "lozinka12", "lozinka12"),
        out=lambda *_: None)
    assert ok is True
    assert s.read().execute("SELECT 1 FROM users").fetchone() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -k operater -v`
Expected: FAIL — `AttributeError: module has no attribute 'page_operater'`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/wizard.py  (dodaj)
from ragspine.web import firstrun

_MIN_PW = 8


def page_operater(spine, *, input_fn=input, out=print) -> bool:
    tui.print_header("2/6  Operater (administrator)", out=out)
    username = tui.prompt_text("Korisničko ime", input_fn=input_fn, out=out)
    while True:
        pw = tui.prompt_text("Lozinka (min 8)", input_fn=input_fn, out=out)
        if len(pw) < _MIN_PW:
            out(f"Lozinka mora imati barem {_MIN_PW} znakova.")
            continue
        pw2 = tui.prompt_text("Ponovi lozinku", input_fn=input_fn, out=out)
        if pw != pw2:
            out("Lozinke se ne podudaraju.")
            continue
        break
    try:
        firstrun.create_first_owner(spine, username, pw)
    except ValueError as e:
        out(f"Greška: {e}")
        return False
    out(f"Administrator '{username}' kreiran.")
    return True
```

```python
# ragspine/ops/wizard.py  — u run(), zamijeni placeholder redak Stranicom 2
    if stage < 2:
        if not page_operater(spine, input_fn=input_fn, out=out):
            out("Setup prekinut na operateru.")
            return
        wizard_state.set_stage(spine, 2)
    out("P1 gotov: preduvjeti + operater. Stranice 3-6 slijede u P2-P4.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Full suite + cyrillic gate**

Run: `pytest tests/test_wizard.py tests/test_wizard_state.py tests/test_firstrun.py tests/test_preflight.py tests/test_tui.py tests/test_security.py tests/test_no_cyrillic.py -q`
Expected: PASS (sve)

- [ ] **Step 6: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): stranica 2 (operater) — kreira admina, validira lozinku, bilježi stage"
```

---

## Self-Review (autor)

**Spec coverage (P1 dio speca):**
- Setup-state/resume (spec §2) → Task 1, 7, 8. ✓
- Gatekeeper na `setup_complete` (spec §2) → Task 2. ✓
- PBKDF2 600k migrabilni (spec §3 str.2) → Task 3. ✓
- Tesseract obavezan + Ollama health-check (spec str.1/str.3) → Task 4. ✓
- Internet=status + ip_mode + (proxy polje surface) → Task 5. (Proxy: `ip_mode`+internet pokriveni; proxy-config polje je UI-detalj str.1 — dodaje se u Task 7 render ako zatreba; nije zaseban blocker.)
- TUI (uzor hermes-agent) → Task 6. ✓
- Str.1 preduvjeti + str.2 operater → Task 7, 8. ✓
- Bootstrap-transakcija ne owner-unique (spec str.2) → već u `create_first_owner` (BEGIN IMMEDIATE, bez UNIQUE(role)). ✓
- Auto-install (winget UAC) — spec str.1: **odgođeno u P1** (samo detekcija+fix string). Auto-install izvršavanje = kraj P2 ili zaseban task; zabilježeno kao svjesno odgađanje (render pokazuje `fix` naredbu). 
- Static-IP `netsh set` (spec str.1) — P1 daje **detekciju** (`ip_mode`); postavljanje statičkog = P3 (mreža). Svjesno.

**Placeholder scan:** nema TBD/TODO; sav kod konkretan. „Stranice 3-6 u P2-P4" su scope granice, ne placeholderi.

**Type consistency:** `wizard_state.get_stage/set_stage/is_complete/mark_complete/reset`, `tui.prompt_*`, `preflight.ollama_ready/internet_ok/requirements`, `wizard.render_preflight/page_preduvjeti/page_operater/run` — imena dosljedna kroz zadatke.

## Runnable check

`pytest tests/test_wizard.py tests/test_wizard_state.py tests/test_firstrun.py tests/test_preflight.py tests/test_tui.py tests/test_security.py -q` mora biti zelen + `python -m ragspine setup --help`.
