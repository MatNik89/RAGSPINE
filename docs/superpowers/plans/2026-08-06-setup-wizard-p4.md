# Setup Wizard P4 (stranice 5-6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dovršiti terminal setup wizard: stranica 5 (mape/mrežni pogoni, UNC) + stranica 6 (sažetak + backup + `setup_complete` + „Pokreni sada" s Edge app-modeom).

**Architecture:** Sve u `ragspine/ops/wizard.py` po postojećem obrascu: `page_X(spine, cfg, *, input_fn, out) -> bool`, injektabilan I/O, `run()` staging preko `wizard_state`. Stranica 5 registrira mape kroz postojeći `ragspine.business.folders.register` (ista tablica koju čita web Postavke → Mrežne mape). Stranica 6 čita postojeće overrides + `ops/backup.create_backup` (VACUUM INTO + verifikacija).

**Tech Stack:** Python 3.11+ stdlib (os, re, subprocess, platform, types), postojeći moduli (`ops/tui`, `ops/backup`, `ops/wizard_state`, `business/folders`, `web/firstrun`). Bez novih dependencyja.

## Global Constraints

- Hrvatski latinica **s dijakriticima** u svim stringovima i komentarima (cyrillic-gate `tests/test_no_cyrillic.py` mora ostati zelen; nikakva ćirilica).
- Bez novih dependencyja.
- Testovi bez mreže, bez pravog stdina (injektiraj `input_fn`), bez pravih subprocessa (injektiraj/mockaj `popen`).
- `wizard_state.mark_complete` poziva se **točno jednom**, iza ZADNJE implementirane stranice (Task 1 ga pomiče iza stranice 5, Task 2 iza stranice 6 — u svakom trenutku grana je konzistentna).
- Commit poruke: hrvatske konvencionalne (`feat(setup): ...`), footer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- I/O obrazac: svaki novi page/helper prima `input_fn=input, out=print` keyword-only i NIKAD ne zove goli `input()`/`print()`.

### Dokumentirana odstupanja od speca (odlučeno, ne raspravlja se u tasku)

1. **SMB kredencijali / DPAPI lozinka = backlog** (isti ruling kao P3 „DPAPI backlog"). Wizard NE sprema SMB lozinke; kad je mapa nedostupna ispisuje `net use ... /user:... * /persistent:no` komandu (`*` tjera net.exe da sam pita lozinku — ne završi u argv), admin je pokrene sam. `ponytail:` komentar s upgrade putom (DPAPI LocalMachine spremanje kredencijala).
2. **Test pristupa pod servisnim identitetom = de-scoped**: servisni račun ne postoji (P3 je de-scopeao sc.exe create; wrapper WinSW/NSSM = backlog). Test pristupa ide kao trenutni korisnik (`os.path.isdir`) + iskrena napomena u ispisu.
3. **Edge app-mode preko `cmd /c start msedge --app=...`** (App Paths registracija — msedge nije na PATH-u); ne-Windows: samo ispiši URL. Puni PWA = backlog (spec §7).
4. **„servis start" u „Pokreni sada"**: servisa nema (v. točku 2) — pokreće se `ragspine serve` **detached** (isti obrazac kao `preflight.start_ollama`: DETACHED_PROCESS na Windowsu, `start_new_session` na POSIX-u).
5. **`RAGSPINE_MOUNT_ROOTS`**: env je trust-granica za web registraciju mapa i to ostaje. Wizard (admin u terminalu) registrira mapu kroz `folders.register` s privremenim scope-om (`types.SimpleNamespace(mount_roots=[root])`), a na kraju stranice ispiše koje korijene treba staviti u `RAGSPINE_MOUNT_ROOTS` da ih i web/servis vidi.

---

## File Structure

- Modify: `ragspine/ops/wizard.py` — dodaju se `_MAPE_ULOGE`, `_net_use_hint`, `page_mape` (Task 1); `render_summary`, `page_gotovo` (Task 2); `_detached_kwargs`, `launch_now` (Task 3); `run()` se proširuje u Taskovima 1-3.
- Modify: `tests/test_wizard.py` — novi testovi po tasku + ažuriranje postojećih `run()` testova (novi pageovi se mockaju, stage asserti rastu).

---

### Task 1: Stranica 5 — mape / mrežni pogoni (`page_mape`)

**Files:**
- Modify: `ragspine/ops/wizard.py` (import blok na vrhu; nove funkcije iza `page_mreza`, tj. iza retka 307; `run()` tail, retci 332-350)
- Test: `tests/test_wizard.py`

**Interfaces:**
- Consumes: `tui.prompt_yes_no/prompt_text/print_header`, `ragspine.business.folders.register` (postojeći: `register(spine, cfg, path, role, label="", user="?")`, scoping preko `cfg.mount_roots`), `wizard_state.set_stage/mark_complete`.
- Produces: `wizard.page_mape(spine, cfg, *, input_fn=input, out=print) -> bool`; `wizard._MAPE_ULOGE: list[tuple[str, str]]`; `wizard._net_use_hint(unc: str) -> str`. `run()` dobiva `if stage < 5` blok; `mark_complete` se pomiče iza njega.

- [ ] **Step 1: Napiši failing testove**

U `tests/test_wizard.py` (iza mreža-testova, prije `test_render_preflight_blocks_on_fail`):

```python
def test_page_mape_preskok(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    ok = wizard.page_mape(s, None, input_fn=_reader("n"), out=lambda *_: None)
    assert ok is True
    from ragspine.business import folders
    assert folders.list_folders(s) == []


def test_page_mape_registrira_mapu(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    d = tmp_path / "klijenti"
    d.mkdir()
    # "d" (poveži), putanja za Klijente, prazno za preostale 3 uloge
    lines = []
    ok = wizard.page_mape(s, None, input_fn=_reader("d", str(d), "", "", ""),
                          out=lines.append)
    assert ok is True
    from ragspine.business import folders
    rows = folders.list_folders(s)
    assert len(rows) == 1 and rows[0]["role"] == "klijenti"
    assert any("RAGSPINE_MOUNT_ROOTS" in l for l in lines)


def test_page_mape_nedostupna_pa_odustane(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    bad = str(tmp_path / "nema")
    # "d", nepostojeća putanja, "n" (ne pokušava ponovno), prazno za ostale 3
    lines = []
    ok = wizard.page_mape(s, None, input_fn=_reader("d", bad, "n", "", "", ""),
                          out=lines.append)
    assert ok is True
    assert any("net use" in l for l in lines)
    from ragspine.business import folders
    assert folders.list_folders(s) == []


def test_page_mape_upozorava_na_slovo_pogona(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    # "d", drive-letter putanja (ne postoji na Linuxu), "n", prazno za ostale 3
    lines = []
    wizard.page_mape(s, None, input_fn=_reader("d", "Z:\\skenovi", "n", "", "", ""),
                     out=lines.append)
    assert any("Slovo pogona" in l for l in lines)


def test_net_use_hint_iz_unc_putanje():
    hint = wizard._net_use_hint(r"\\nas\ured\klijenti\2026")
    assert r"net use \\nas\ured" in hint
    assert "*" in hint and "/persistent:no" in hint
```

- [ ] **Step 2: Pokreni testove — moraju pasti**

Run: `python -m pytest tests/test_wizard.py -k "mape or net_use" -v`
Expected: FAIL/ERROR — `wizard` nema atribut `page_mape` / `_net_use_hint`.

- [ ] **Step 3: Implementacija u `ragspine/ops/wizard.py`**

Na vrh modula u postojeći import blok dodaj `types` (stdlib, abecedno uz ostale).

Iza `page_mreza` (redak 307) dodaj:

```python
# Stranica 5 (spec): uloga (folders.role string) → naziv za prompt.
_MAPE_ULOGE = [
    ("klijenti", "Klijenti"),
    ("propisi", "Propisi"),
    ("skener", "Zajednički skenovi"),
    ("program", "Knjigovodstveni program"),
]


def _net_use_hint(unc: str) -> str:
    """`net use` komanda za SMB prijavu; `*` = net.exe sam pita lozinku (ne
    završi u argv/process-listi), /persistent:no = bez naslijeđenih mapiranja.
    ponytail: DPAPI spremanje SMB kredencijala = backlog; do tada admin
    komandu pokreće ručno, wizard lozinke ne dira."""
    share = "\\".join(unc.split("\\")[:4])   # \\server\share (bez podmapa)
    return f"net use {share} /user:DOMENA\\korisnik * /persistent:no"


def page_mape(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 5: mrežne mape po ulogama (UNC putanje), preskočivo.
    Test pristupa ide kao trenutni korisnik (servisni račun ne postoji —
    wrapper backlog, v. winsvc); registracija ide u istu folders tablicu
    koju koristi web Postavke → Mrežne mape."""
    from ragspine.business import folders
    tui.print_header("5/6  Mape / mrežni pogoni", out=out)
    if not tui.prompt_yes_no("Poveži mrežne mape sada? (kasnije: Postavke → Mrežne mape)",
                             default=True, input_fn=input_fn, out=out):
        return True
    registered = []
    for role, naziv in _MAPE_ULOGE:
        while True:
            path = tui.prompt_text(
                f"{naziv} — UNC putanja (\\\\server\\share\\...; prazno = preskoči)",
                input_fn=input_fn, out=out)
            if not path:
                break
            if re.match(r"^[A-Za-z]:", path):
                out("  ⚠ Slovo pogona (npr. Z:) servisni račun ne vidi — koristi UNC putanju.")
            if not os.path.isdir(path):
                out(f"  ✗ Nedostupno: {path}")
                out(f"    Ako share traži prijavu, u drugom prozoru pokreni: {_net_use_hint(path)}")
                if tui.prompt_yes_no("  Pokušaj ponovno?", default=True,
                                     input_fn=input_fn, out=out):
                    continue
                break
            root = os.path.realpath(path)
            try:
                folders.register(spine, types.SimpleNamespace(mount_roots=[root]),
                                 path, role, label=naziv, user="setup")
            except ValueError as e:
                out(f"  ✗ {e}")
                break
            out(f"  ✓ {naziv}: {path}")
            registered.append(root)
            break
    if registered:
        out("")
        out("Da web sučelje (i budući servis) vidi ove mape, postavi env varijablu:")
        out(f"  RAGSPINE_MOUNT_ROOTS={','.join(registered)}")
    return True
```

U `run()` zamijeni tail (postojeće retke od `if stage < 4:` do kraja funkcije) ovim:

```python
        if stage < 4:
            if not page_mreza(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na mreži. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 4)
        if stage < 5:
            if not page_mape(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na mapama. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 5)
    except (EOFError, KeyboardInterrupt):
        # non-TTY / piped stdin (npr. servis bez terminala) — bez tracebacka.
        # ponytail: run() ostaje `-> None`; pozivatelj (_cmd_setup) ne detektira
        # ovaj slučaj posebno pa CLI izlazi s 0. Upgrade path: vratiti bool/kod
        # ako se pokaže da nešto ovisi o exit statusu.
        out("")
        out("Setup zahtijeva interaktivni terminal. Pokreni `ragspine setup` u terminalu; "
            "stanje je spremljeno — nastavlja gdje je stao.")
        return
    # Stranica 6 (sažetak) stiže u sljedećem tasku; mark_complete ide iza
    # ZADNJE implementirane stranice.
    wizard_state.mark_complete(spine)
    out("Setup dovršen: preduvjeti + operater + model + mreža + mape. Sažetak (6/6) slijedi.")
```

- [ ] **Step 4: Ažuriraj postojeće `run()` testove**

U `tests/test_wizard.py`:
- `test_run_success_marks_setup_complete`: dodaj `monkeypatch.setattr(wizard, "page_mape", lambda *a, **k: True)`; assert `ws.get_stage(spine) == 5`.
- `test_run_reaches_stage4_and_completes`: u petlju mockova dodaj `"page_mape"`; assert `ws.get_stage(s) == 5`. Preimenuj u `test_run_reaches_stage5_and_completes`.
- `test_run_no_complete_when_mreza_page_cancelled`: bez izmjene (mreža otkazana prije mapa).
- `test_run_resume_from_stage2_runs_only_model_page`: dodaj mock `page_mape` s `ran.append("p5")`; assert `ran == ["p3", "p4", "p5"]`.

- [ ] **Step 5: Pokreni ciljane testove**

Run: `python -m pytest tests/test_wizard.py -v`
Expected: PASS (svi, uključujući ažurirane run testove).

- [ ] **Step 6: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): stranica 5 (mape/UNC) — folders.register + net use uputa; mark_complete iza"
```

---

### Task 2: Stranica 6 — sažetak + backup (`page_gotovo`) + `mark_complete` pomak

**Files:**
- Modify: `ragspine/ops/wizard.py` (import blok; nove funkcije iza `page_mape`; `run()` tail iz Taska 1)
- Test: `tests/test_wizard.py`

**Interfaces:**
- Consumes: `wizard.page_mape` (Task 1), `ops.backup.create_backup(cfg, stamp=None) -> dict` (ključevi `name/path/size`; baca `RuntimeError` na neuspjelu verifikaciju), `business.folders.list_folders(spine) -> list[dict]` (ključevi `role/path/...`), `firstrun.needs_onboarding(spine) -> bool`, overrides `("model","model")`, `("model","embed_model")`, `("net","host"/"port"/"cert_path")`.
- Produces: `wizard.render_summary(spine, cfg, *, out=print) -> None`; `wizard.page_gotovo(spine, cfg, *, input_fn=input, out=print) -> bool`. `run()`: `if stage < 6` blok; `mark_complete` točno jednom, iza stranice 6. Cfg atributi koje stranica čita: `cfg.db_path`, `cfg.data_dir`.

- [ ] **Step 1: Napiši failing testove**

```python
def test_render_summary_pokazuje_konfigurirano_i_preskoceno(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    s.set_override("net", "host", "192.168.1.7")
    s.set_override("net", "port", "8443")
    s.set_override("model", "model", "qwen2.5:7b")

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)
    lines = []
    wizard.render_summary(s, _Cfg(), out=lines.append)
    text = "\n".join(lines)
    assert "qwen2.5:7b" in text
    assert "https://192.168.1.7:8443" in text
    assert "Mape" in text            # preskočeno → uputa na Postavke
    assert "preskočeno" in text


def test_page_gotovo_backup_da(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)
    made = []
    monkeypatch.setattr(wizard.backup, "create_backup",
                        lambda cfg: made.append(1) or
                        {"name": "x", "path": str(tmp_path / "x.db"), "size": 7})
    lines = []
    ok = wizard.page_gotovo(s, _Cfg(), input_fn=_reader(""), out=lines.append)
    assert ok is True and made == [1]
    text = "\n".join(lines)
    assert ".ollama" in text and "secret" in text


def test_page_gotovo_backup_greska_ne_rusi(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))

    class _Cfg:
        db_path = str(tmp_path / "t.db")
        data_dir = str(tmp_path)

    def _boom(cfg):
        raise RuntimeError("verifikacija pala")
    monkeypatch.setattr(wizard.backup, "create_backup", _boom)
    lines = []
    ok = wizard.page_gotovo(s, _Cfg(), input_fn=_reader("d"), out=lines.append)
    assert ok is True
    assert any("ragspine backup" in l for l in lines)
```

- [ ] **Step 2: Pokreni testove — moraju pasti**

Run: `python -m pytest tests/test_wizard.py -k "summary or gotovo" -v`
Expected: FAIL/ERROR — `wizard` nema `render_summary`/`page_gotovo`/`backup`.

- [ ] **Step 3: Implementacija**

U import blok wizarda dodaj `backup` u postojeći `from ragspine.ops import ...` redak.

Iza `page_mape` dodaj:

```python
def render_summary(spine, cfg, *, out=print) -> None:
    """Sažetak stranica 1-5: konfigurirano / preskočeno + gdje dodati kasnije."""
    from ragspine.business import folders
    admin = "✓ kreiran" if not firstrun.needs_onboarding(spine) else "✗ nije kreiran"
    model = spine.get_override("model", "model") or ""
    emb = spine.get_override("model", "embed_model") or ""
    host = spine.get_override("net", "host") or ""
    port = spine.get_override("net", "port") or ""
    cert = spine.get_override("net", "cert_path") or ""
    out("Sažetak:")
    out(f"  Administrator: {admin}")
    out(f"  LLM model: {model or '— preskočeno (Postavke → Model)'}")
    out(f"  Embedding: {emb or '— preskočeno (Postavke → Model)'}")
    out(f"  Mreža: {f'https://{host}:{port}' if host else '— zadano (Postavke → Mreža)'}")
    out(f"  HTTPS cert: {cert or '— nema (ragspine setup, stranica 4)'}")
    mape = folders.list_folders(spine)
    if mape:
        for m in mape:
            out(f"  Mapa [{m['role']}]: {m['path']}")
    else:
        out("  Mape: — preskočeno (Postavke → Mrežne mape)")


def page_gotovo(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 6: sažetak + konkretan backup/restore. Uvijek vraća True —
    zadnja stranica ne smije blokirati dovršetak setupa."""
    tui.print_header("6/6  Gotovo — sažetak i sigurnosne kopije", out=out)
    render_summary(spine, cfg, out=out)
    out("")
    out("Sigurnosne kopije (spremi na vanjski medij/NAS — bez ovoga restore ne vraća sustav):")
    out(f"  • Baza: {cfg.db_path} — snapshot naredbom: ragspine backup")
    out(f"  • Tajni ključ (JWT): {Path(cfg.data_dir) / 'secret'} — bez njega prijave "
        "nakon restorea ne rade; kopiraj ga UZ bazu")
    out(r"  • Ollama modeli: %USERPROFILE%\.ollama\models — bez njih RAG ne radi "
        "ni uz vraćenu bazu i ključ")
    out("  • Restore provjeri na drugom stroju: ragspine restore <ime> (server zaustavljen)")
    if tui.prompt_yes_no("Napravi verificirani snapshot baze sada?", default=True,
                         input_fn=input_fn, out=out):
        try:
            res = backup.create_backup(cfg)
            out(f"  ✓ {res['path']} ({res['size']} B, verificiran quick_checkom)")
        except Exception as e:
            out(f"  ⚠ Snapshot nije uspio: {e} — pokreni `ragspine backup` ručno.")
    return True
```

U `run()`: iza `if stage < 5` bloka (unutar `try`) dodaj:

```python
        if stage < 6:
            if not page_gotovo(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na sažetku. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 6)
```

a tail iza `except` bloka zamijeni s:

```python
    wizard_state.mark_complete(spine)
    out("✓ Setup dovršen (6/6) — web sučelje je spremno.")
```

- [ ] **Step 4: Ažuriraj postojeće `run()` testove**

- `test_run_success_marks_setup_complete`: dodaj mock `page_gotovo` → True; assert `ws.get_stage(spine) == 6`.
- `test_run_reaches_stage5_and_completes` (ime iz Taska 1): dodaj `"page_gotovo"` u petlju mockova; assert stage `== 6`; preimenuj u `test_run_reaches_stage6_and_completes`.
- `test_run_resume_from_stage2_runs_only_model_page`: dodaj mock `page_gotovo` s `ran.append("p6")`; assert `ran == ["p3", "p4", "p5", "p6"]`.

- [ ] **Step 5: Pokreni ciljane testove**

Run: `python -m pytest tests/test_wizard.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): stranica 6 (sažetak+backup) — mark_complete iza zadnje stranice"
```

---

### Task 3: „Pokreni RAGSPINE sada?" — serve detached + Edge app-mode (`launch_now`)

**Files:**
- Modify: `ragspine/ops/wizard.py` (import blok; nove funkcije iza `page_gotovo`; jedan redak u `run()` tailu)
- Test: `tests/test_wizard.py`

**Interfaces:**
- Consumes: overrides `("net","host"/"port")`, `preflight.local_ip()`, obrazac detached spawna iz `preflight.start_ollama` (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP na Windowsu, `start_new_session` na POSIX-u).
- Produces: `wizard._detached_kwargs() -> dict`; `wizard.launch_now(spine, cfg, *, input_fn=input, out=print, popen=subprocess.Popen) -> None`. `run()` ga poziva jednom, iza `mark_complete`.

- [ ] **Step 1: Napiši failing testove**

```python
def test_launch_now_odbijen_ne_pokrece_nista(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    calls = []
    lines = []
    wizard.launch_now(s, None, input_fn=_reader("n"), out=lines.append,
                      popen=lambda *a, **k: calls.append(a))
    assert calls == []
    assert any("ragspine serve" in l for l in lines)


def test_launch_now_windows_pokrece_serve_i_edge(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    s.set_override("net", "host", "192.168.1.7")
    s.set_override("net", "port", "8443")
    monkeypatch.setattr(wizard.platform, "system", lambda: "Windows")
    calls = []
    wizard.launch_now(s, None, input_fn=_reader(""), out=lambda *_: None,
                      popen=lambda cmd, **k: calls.append(cmd))
    assert len(calls) == 2
    assert calls[0][-1] == "serve"
    assert calls[1][:4] == ["cmd", "/c", "start", ""]
    assert "--app=https://192.168.1.7:8443" in calls[1]


def test_launch_now_bind_sve_mreze_koristi_lan_ip(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))
    s.set_override("net", "host", "0.0.0.0")
    s.set_override("net", "port", "8443")
    monkeypatch.setattr(wizard.preflight, "local_ip", lambda: "192.168.1.7")
    lines = []
    wizard.launch_now(s, None, input_fn=_reader("n"), out=lines.append,
                      popen=lambda *a, **k: None)
    assert any("https://192.168.1.7:8443" in l for l in lines)


def test_launch_now_oserror_ne_rusi(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))

    def _boom(cmd, **k):
        raise OSError("nema binarke")
    lines = []
    wizard.launch_now(s, None, input_fn=_reader(""), out=lines.append, popen=_boom)
    assert any("ragspine serve" in l for l in lines)


def test_launch_now_eof_tretira_kao_ne(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))

    def _eof(_=""):
        raise EOFError()
    calls = []
    wizard.launch_now(s, None, input_fn=_eof, out=lambda *_: None,
                      popen=lambda *a, **k: calls.append(a))   # ne smije propagirati
    assert calls == []
```

- [ ] **Step 2: Pokreni testove — moraju pasti**

Run: `python -m pytest tests/test_wizard.py -k launch_now -v`
Expected: FAIL/ERROR — `wizard` nema `launch_now`.

- [ ] **Step 3: Implementacija**

U import blok wizarda dodaj `platform` i `subprocess` (stdlib, abecedno).

Iza `page_gotovo` dodaj:

```python
def _detached_kwargs() -> dict:
    """Popen kwargs da dijete preživi zatvaranje wizard konzole (isti obrazac
    kao preflight.start_ollama; getattr fallback za testove na Linuxu s
    mockanim platform.system())."""
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kwargs["start_new_session"] = True
    return kwargs


def launch_now(spine, cfg, *, input_fn=input, out=print, popen=subprocess.Popen) -> None:
    """Ponudi start servera (detached) + Edge app-prozor. Poziva se IZA
    mark_complete — nikakav kvar ovdje ne smije poništiti dovršeni setup.
    ponytail: pravi servis (autostart) čeka WinSW/NSSM wrapper (v. winsvc);
    do tada je ovo ručni start koji preživi zatvaranje konzole."""
    host = spine.get_override("net", "host") or "127.0.0.1"
    port = spine.get_override("net", "port") or "8443"
    url_host = preflight.local_ip() if host == "0.0.0.0" else host
    url = f"https://{url_host}:{port}"
    try:
        start = tui.prompt_yes_no(f"Pokreni RAGSPINE sada? ({url})", default=True,
                                  input_fn=input_fn, out=out)
    except (EOFError, KeyboardInterrupt):
        start = False
    if not start:
        out(f"Kasnije: `ragspine serve` pa otvori {url}")
        return
    exe = shutil.which("ragspine")
    cmd = [exe, "serve"] if exe else [sys.executable, "-m", "ragspine", "serve"]
    try:
        popen(cmd, **_detached_kwargs())
    except OSError as e:
        out(f"⚠ Server nije pokrenut ({e}) — pokreni ručno: ragspine serve")
        return
    out(f"✓ Server pokrenut u pozadini — {url}")
    if platform.system() == "Windows":
        try:
            # start preko App Paths (msedge nije na PATH-u); "" = naslov prozora
            popen(["cmd", "/c", "start", "", "msedge", f"--app={url}"],
                  **_detached_kwargs())
            out("Otvaram Edge app-prozor...")
        except OSError:
            out(f"Otvori ručno u pregledniku: {url}")
    else:
        out(f"Otvori u pregledniku: {url}")
```

U `run()` tailu, iza `mark_complete`/završne poruke dodaj:

```python
    launch_now(spine, cfg, input_fn=input_fn, out=out)
```

- [ ] **Step 4: Ažuriraj postojeće `run()` testove**

`run()` sada zove `launch_now` — testovi koji zovu `run()` do kraja (`test_run_success_marks_setup_complete`, `test_run_reaches_stage6_and_completes`, `test_run_resume_from_stage2_runs_only_model_page`) dobivaju `monkeypatch.setattr(wizard, "launch_now", lambda *a, **k: None)`.

- [ ] **Step 5: Pokreni ciljane testove, pa puni suite**

Run: `python -m pytest tests/test_wizard.py -v` → PASS
Run: `python -m pytest -q` → očekivano sve zeleno (baseline 1114 passed, 1 skipped + novi).

- [ ] **Step 6: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): Pokreni sada — ragspine serve detached + Edge app-mode"
```

---

## Self-Review (obavljen pri pisanju plana)

1. **Spec coverage (§ stranica 5):** UNC (warn na drive-letter) ✓ T1; uloge (4) ✓ T1; SMB kredencijali → dokumentirano odstupanje 1 (net use hint) ✓ T1; test pristupa → odstupanje 2 (isdir kao trenutni korisnik) ✓ T1; iste uloge u web Postavke (ista folders tablica) ✓ T1; preskočivo ✓ T1.
2. **Spec coverage (§ stranica 6):** sažetak ✓ T2; konkretan backup (DB, secret, Ollama modeli, verificiran snapshot VACUUM INTO, restore provjera) ✓ T2; `setup_complete=true` ✓ T2; „Pokreni sada" + Edge `--app` ✓ T3 (servis start → odstupanje 4).
3. **Placeholderi:** nema.
4. **Konzistentnost tipova/imena:** `page_mape/page_gotovo/launch_now` potpisi prate postojeći obrazac; `folders.register` potpis provjeren u kodu; `backup.create_backup` vraća `{name,path,size}` (provjereno); overrides ključevi provjereni (`model/model`, `net/host|port|cert_path`).
