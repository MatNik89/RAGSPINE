# Setup wizard upgrade grana — implementacijski plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ragspine setup` na postojećoj bazi (korisnici postoje, setup_complete nema) eksplicitno ponudi preuzimanje postojeće konfiguracije umjesto prolaska kroz svih 6 stranica.

**Architecture:** Nova funkcija `page_upgrade` u `ragspine/ops/wizard.py` (header + postojeći `render_summary` + da/ne prompt s pametnim defaultom); `run()` je zove kad je `stage == 0` i korisnici postoje. Na „da" → `mark_complete` + `launch_now`, bez stranica.

**Tech Stack:** Python 3.11+, postojeći `ops/tui.py` prompti, pytest.

Spec: `docs/superpowers/specs/2026-08-06-wizard-upgrade-design.md`.

## Global Constraints

- Hrvatski (latinica s dijakriticima) u komentarima, porukama, testnim imenima i commit porukama.
- Bez novih ovisnosti.
- Testovi bez mreže, bez stdina, bez pravih subprocessa (postojeći `_reader` / `out=lines.append` obrasci u `tests/test_wizard.py`).
- Puni test suite se vrti U PRVOM PLANU (foreground), ne u backgroundu.
- Commit poruke: hrvatske konvencionalne s footerom `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `page_upgrade` funkcija + unit testovi

**Files:**
- Modify: `ragspine/ops/wizard.py` (nova funkcija odmah IZNAD `def run(`, ~linija 490)
- Test: `tests/test_wizard.py` (dodaj na kraj datoteke)

**Interfaces:**
- Consumes: postojeće `render_summary(spine, cfg, *, out)`, `tui.print_header`, `tui.prompt_yes_no(pitanje, default=..., input_fn=..., out=...)`, `spine.get_override(module, key)`.
- Produces: `page_upgrade(spine, cfg, *, input_fn=input, out=print) -> bool` — True = preuzmi postojeću konfiguraciju (pozivatelj označava setup dovršenim), False = nastavi normalni wizard. Task 2 je zove iz `run()`.

- [ ] **Step 1: Napiši padajuće testove**

Na kraj `tests/test_wizard.py` (datoteka već importa `init_spine`, `wizard`, `ws`, `firstrun` i ima `_reader`):

```python
def _legacy_spine(tmp_path):
    """Baza kakvu ostavi instalacija prije wizarda: admin + model + mreža."""
    s = init_spine(str(tmp_path / "t.db"))
    firstrun.create_first_owner(s, "admin", "lozinka123")
    s.set_override("model", "model", "qwen2.5:7b")
    s.set_override("net", "host", "192.168.1.7")
    return s


class _UpgCfg:
    db_path = ""
    data_dir = ""


def test_page_upgrade_da_vraca_true(tmp_path):
    s = _legacy_spine(tmp_path)
    lines = []
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader("d"), out=lines.append)
    assert ok is True
    text = "\n".join(lines)
    assert "Postojeća baza" in text
    assert "qwen2.5:7b" in text          # render_summary je prikazan


def test_page_upgrade_ne_vraca_false(tmp_path):
    s = _legacy_spine(tmp_path)
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader("n"), out=lambda *_: None)
    assert ok is False


def test_page_upgrade_default_da_kad_je_sve_postavljeno(tmp_path):
    s = _legacy_spine(tmp_path)
    # prazan unos = default; admin+model+host postavljeni → default da
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader(""), out=lambda *_: None)
    assert ok is True


def test_page_upgrade_default_ne_kad_fali_model(tmp_path):
    s = init_spine(str(tmp_path / "t.db"))
    firstrun.create_first_owner(s, "admin", "lozinka123")   # samo admin, bez modela/mreže
    ok = wizard.page_upgrade(s, _UpgCfg(), input_fn=_reader(""), out=lambda *_: None)
    assert ok is False
```

- [ ] **Step 2: Provjeri da padaju**

Run: `python -m pytest tests/test_wizard.py -q -k page_upgrade`
Expected: 4 FAILED/ERROR s `AttributeError: ... has no attribute 'page_upgrade'`

- [ ] **Step 3: Implementiraj `page_upgrade`**

U `ragspine/ops/wizard.py`, neposredno iznad `def run(`:

```python
def page_upgrade(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Postojeća baza bez setup_complete flaga (instalacija prije wizarda):
    spec traži „detektiraj, ponudi migraciju, ne novi setup". Migracija =
    preuzmi postojeću konfiguraciju (shema je ista, nema podataka za seljenje).
    True = preuzeto (pozivatelj označava setup dovršenim), False = normalni wizard."""
    tui.print_header("Postojeća baza otkrivena", out=out)
    render_summary(spine, cfg, out=out)
    out("")
    # Default „da" samo za klasičnu legacy instalaciju (admin je okidač u run();
    # model+host potvrđuju da je sustav bio konfiguriran, ne napola postavljen).
    default = bool(spine.get_override("model", "model")
                   and spine.get_override("net", "host"))
    return tui.prompt_yes_no(
        "Preuzmi postojeće postavke i označi setup dovršenim? (ne = prođi setup)",
        default=default, input_fn=input_fn, out=out)
```

Napomena: `tui.prompt_yes_no` je postojeći; provjeri točan potpis (poziva se kao u
`page_mape`: `tui.prompt_yes_no(pitanje, default=True, input_fn=input_fn, out=out)`).

- [ ] **Step 4: Provjeri da prolaze**

Run: `python -m pytest tests/test_wizard.py -q -k page_upgrade`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(wizard): page_upgrade — ponuda preuzimanja postojeće konfiguracije

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: okidač u `run()` + integracijski testovi

**Files:**
- Modify: `ragspine/ops/wizard.py` — `run()` (~linija 490)
- Test: `tests/test_wizard.py` (dodaj na kraj)

**Interfaces:**
- Consumes: `page_upgrade(spine, cfg, *, input_fn, out) -> bool` iz Taska 1; postojeći `wizard_state.get_stage/mark_complete`, `firstrun.needs_onboarding(spine)`, `launch_now(spine, cfg, *, input_fn, out)`.
- Produces: ništa novo za druge taskove; mijenja tok `run()`.

- [ ] **Step 1: Napiši padajuće testove**

Na kraj `tests/test_wizard.py` (koristi `_legacy_spine` i `_UpgCfg` iz Taska 1; postojeći integracijski testovi `run()`-a monkeypatchaju stranice — isti obrazac):

```python
def _stub_pages(monkeypatch, ran):
    for p in ("page_preduvjeti", "page_operater", "page_model",
              "page_mreza", "page_mape", "page_gotovo"):
        monkeypatch.setattr(wizard, p,
                            lambda *a, _p=p, **k: ran.append(_p) or True)
    monkeypatch.setattr(wizard, "launch_now", lambda *a, **k: None)


def test_run_legacy_baza_da_preskace_stranice(tmp_path, monkeypatch):
    s = _legacy_spine(tmp_path)
    ran = []
    _stub_pages(monkeypatch, ran)
    wizard.run(s, _UpgCfg(), input_fn=_reader("d"), out=lambda *_: None)
    assert ran == []
    assert ws.is_complete(s) is True


def test_run_legacy_baza_ne_ide_normalni_wizard(tmp_path, monkeypatch):
    s = _legacy_spine(tmp_path)
    ran = []
    _stub_pages(monkeypatch, ran)
    wizard.run(s, _UpgCfg(), input_fn=_reader("n"), out=lambda *_: None)
    assert "page_preduvjeti" in ran and "page_gotovo" in ran
    assert ws.is_complete(s) is True


def test_run_svjeza_baza_bez_ponude(tmp_path, monkeypatch):
    s = init_spine(str(tmp_path / "t.db"))          # bez korisnika
    ran = []
    _stub_pages(monkeypatch, ran)
    ponuda = []
    monkeypatch.setattr(wizard, "page_upgrade",
                        lambda *a, **k: ponuda.append(1) or True)
    wizard.run(s, _UpgCfg(), input_fn=_reader(), out=lambda *_: None)
    assert ponuda == []
    assert "page_preduvjeti" in ran


def test_run_resume_bez_ponude(tmp_path, monkeypatch):
    s = _legacy_spine(tmp_path)
    ws.set_stage(s, 1)                               # resume, ne upgrade
    ran = []
    _stub_pages(monkeypatch, ran)
    ponuda = []
    monkeypatch.setattr(wizard, "page_upgrade",
                        lambda *a, **k: ponuda.append(1) or True)
    wizard.run(s, _UpgCfg(), input_fn=_reader(), out=lambda *_: None)
    assert ponuda == []
    assert "page_operater" in ran and "page_preduvjeti" not in ran
```

- [ ] **Step 2: Provjeri da padaju**

Run: `python -m pytest tests/test_wizard.py -q -k "legacy_baza or svjeza_baza or resume_bez"`
Expected: `test_run_legacy_baza_da_preskace_stranice` FAILED (stranice se izvode, complete preko svih 6); ostali mogu proći — bitno je da prvi pada.

- [ ] **Step 3: Implementiraj okidač u `run()`**

U `run()`, odmah nakon `out(f"RAGSPINE setup (nastavak od koraka {stage + 1}).")`, UNUTAR postojećeg `try:` bloka kao prvi korak (prije `if stage < 1:`):

```python
        # Upgrade grana (spec str.1): postojeća baza — korisnici postoje, a
        # setup nikad nije dovršen. Resume (stage>0) nije upgrade slučaj.
        if stage == 0 and not firstrun.needs_onboarding(spine):
            if page_upgrade(spine, cfg, input_fn=input_fn, out=out):
                wizard_state.mark_complete(spine)
                out("✓ Postojeća konfiguracija preuzeta — setup dovršen.")
                launch_now(spine, cfg, input_fn=input_fn, out=out)
                return
```

Poruka „RAGSPINE setup (nastavak od koraka 1)." prije ponude je prihvatljiva (stage je 0). EOFError/KeyboardInterrupt iz prompta hvata postojeći except oko cijelog tijela — non-TTY dobije postojeću poruku o interaktivnom terminalu, stanje netaknuto.

- [ ] **Step 4: Provjeri da prolaze + puni suite u prvom planu**

Run: `python -m pytest tests/test_wizard.py -q` — svi prolaze.
Run: `python -m pytest -q` (U PRVOM PLANU) — bez novih padova.

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(wizard): upgrade okidač u run() — postojeća baza preskače setup na potvrdu

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
