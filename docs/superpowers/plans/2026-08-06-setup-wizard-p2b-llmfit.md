# Setup Wizard P2b — llmfit kao izvor modela (katalog van) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stranica 3 wizarda više NE koristi ručni `MODEL_CATALOG` — izvor modela je **llmfit** (`--json`): on detektira hardver, izračuna najbolju kvantizaciju (`best_quant`) i fit; RAGSPINE to samo lijepo prikaže (🟢🟡🔴, opis, brzina, ⭐ preporuka po score-u). Katalog i pripadna logika se brišu.

**Architecture:** llmfit (MIT, Rust binary distribuiran kao pip wheel s `__main__.py` wrapperom) postaje regularna dependencija ragspinea — instalira se zajedno s njim, radi offline nakon instalacije. `preflight.llmfit_models(cfg)` zove `python -m llmfit --json` kroz postojeći `run_isolated`, filtrira na modele s `ollama_name` (vrtimo ih kroz Ollamu) i chat-kategorije, mapira u naš red-oblik. `page_model` renderira te retke; kad llmfit nije dostupan/pukne → postojeća „preskoči, postavi kasnije" grana (ne zaglavi). `MODEL_CATALOG`/`model_fits`/`recommend_chat_model` se brišu; `fit_pill` OSTAJE (koristi ga `choose_embed_model` za embedding).

**Tech Stack:** Python 3.11+ stdlib + nova dependencija `llmfit>=1.1` (odluka korisnika — iznimka od dosadašnjeg „bez novih deps").

**Izmjereno na stvarnom llmfit 1.1.x izlazu (ovaj stroj):** top-level `{"models": [...], "system": {...}}`; 4476 modela, 137 s `ollama_name`, 54 Chat; `fit_label` ∈ {"Good","Marginal","Too Tight"}; `category` ∈ {Chat, Coding, Embedding, General, Multimodal, Reasoning}; po modelu: `best_quant`, `memory_required_gb`, `estimated_tps`, `score` (float), `use_case`, `parameter_count`.

## Global Constraints

- Jezik koda/komentara/UI stringova: hrvatski (latinica, S dijakriticima — č ć š ž đ). Cyrillic-gate `tests/test_no_cyrillic.py` zelen.
- Python floor: 3.11+.
- Jedina nova dependencija: `llmfit>=1.1` u `[project] dependencies`. Ništa drugo.
- Testovi bez mreže/stdina i BEZ pravog llmfit poziva (mockaj `run_isolated` odnosno `preflight.llmfit_models`).
- `fit_pill` se NE briše (koristi ga `choose_embed_model`); briše se `MODEL_CATALOG`, `model_fits`, `recommend_chat_model`, `_params_b`.
- Mapa pillova: `"Good"→"🟢"`, `"Marginal"→"🟡"`, `"Too Tight"→"🔴"`, ostalo `"?"`.
- Chat-kategorije za izbor: `{"Chat", "Coding", "Reasoning", "General"}`; prikaži samo `fit_label` ∈ {"Good","Marginal"}; sortiraj po `score` silazno; cap 12 redaka; ⭐ preporuka = prvi (najveći score).
- Skip-grane i dalje vraćaju True (spec: „ne zaglavi"); llmfit nedostupan/pukne → ista grana kao „Ollama nedostupna".

---

### Task 1: llmfit dependencija + `preflight.llmfit_models`

**Files:**
- Modify: `pyproject.toml:9` (dependencies)
- Modify: `ragspine/ops/preflight.py` (nova funkcija, iza `ollama_pull`)
- Test: `tests/test_preflight.py` (dodaj)

**Interfaces:**
- Consumes: `ragspine.core.subproc.run_isolated(cmd: list[str], timeout: int = 60, ...) -> tuple[int, str, str]` (postoji).
- Produces: `llmfit_models(cfg=None) -> list[dict] | None` — None kad llmfit nije dostupan/pukne/neparsabilan; inače lista redaka:
  `{"name": str, "ollama_name": str, "category": str, "fit_label": str, "best_quant": str, "memory_gb": float, "tps": float, "score": float, "use_case": str, "params": str}` — filtrirano (ollama_name truthy, category ∈ CHAT_KATEGORIJE, fit_label ∈ {"Good","Marginal"}), sortirano po score silazno, max 12.
- Konstante: `_LLMFIT_CHAT_CATS = {"Chat", "Coding", "Reasoning", "General"}`, `_LLMFIT_MAX_ROWS = 12`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj)
def _llmfit_json(models):
    import json
    return json.dumps({"models": models, "system": {}})


def _lm(name, ollama, cat="Chat", fit="Good", score=50.0):
    return {"name": name, "ollama_name": ollama, "category": cat,
            "fit_label": fit, "best_quant": "Q4_K_M", "memory_required_gb": 4.7,
            "estimated_tps": 12.0, "score": score, "use_case": "chat",
            "parameter_count": "7B"}


def test_llmfit_models_filters_sorts_caps(monkeypatch):
    models = [
        _lm("hf/a", "a:7b", score=60.0),
        _lm("hf/b", None),                             # bez ollama_name -> van
        _lm("hf/c", "c:3b", cat="Embedding"),          # kriva kategorija -> van
        _lm("hf/d", "d:14b", fit="Too Tight"),         # ne stane -> van
        _lm("hf/e", "e:7b", cat="Reasoning", score=90.0),
    ] + [_lm(f"hf/x{i}", f"x{i}:1b", score=float(i)) for i in range(15)]
    monkeypatch.setattr(pf, "run_isolated",
                        lambda cmd, timeout=60: (0, _llmfit_json(models), ""))
    rows = pf.llmfit_models()
    assert rows is not None
    assert len(rows) == 12                              # cap
    assert rows[0]["ollama_name"] == "e:7b"             # najveci score prvi
    assert rows[1]["ollama_name"] == "a:7b"
    names = {r["ollama_name"] for r in rows}
    assert None not in names and "c:3b" not in names and "d:14b" not in names
    assert rows[0]["memory_gb"] == 4.7 and rows[0]["tps"] == 12.0


def test_llmfit_models_none_when_binary_fails(monkeypatch):
    monkeypatch.setattr(pf, "run_isolated", lambda cmd, timeout=60: (1, "", "boom"))
    assert pf.llmfit_models() is None


def test_llmfit_models_none_on_garbage(monkeypatch):
    monkeypatch.setattr(pf, "run_isolated", lambda cmd, timeout=60: (0, "nije json", ""))
    assert pf.llmfit_models() is None
```

Napomena: `run_isolated` mora biti dostupan kao `pf.run_isolated` (module-level import u preflight.py) da monkeypatch radi — provjeri kako ga preflight trenutno importa (`_ip_mode` ga importa lokalno; podigni na module-level `from ragspine.core.subproc import run_isolated` i ukloni lokalni import u `_ip_mode`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k llmfit_models -v`
Expected: FAIL — `llmfit_models` ne postoji

- [ ] **Step 3: Write minimal implementation**

`pyproject.toml` red 9 — dodaj `"llmfit>=1.1"`:

```toml
dependencies = ["fastapi>=0.110", "uvicorn>=0.29", "pydantic>=2", "python-multipart>=0.0.9", "cryptography", "llmfit>=1.1"]
```

`ragspine/ops/preflight.py` (module-level: `import sys` ako ga nema; `from ragspine.core.subproc import run_isolated`; ukloni lokalni import u `_ip_mode`):

```python
_LLMFIT_CHAT_CATS = {"Chat", "Coding", "Reasoning", "General"}
_LLMFIT_MAX_ROWS = 12


def llmfit_models(cfg=None) -> list[dict] | None:
    """Modeli po llmfitu: on detektira hardver i izračuna najbolju kvantizaciju.
    Vraćamo samo Ollama-pokretljive chat modele koji stanu (Good/Marginal),
    sortirane po score-u. None kad llmfit nije dostupan ili izlaz ne valja."""
    import json
    try:
        rc, out, _err = run_isolated([sys.executable, "-m", "llmfit", "--json"],
                                     timeout=60)
        if rc != 0:
            return None
        data = json.loads(out)
    except Exception:
        return None
    rows = []
    for m in data.get("models", []):
        if not m.get("ollama_name"):
            continue
        if m.get("category") not in _LLMFIT_CHAT_CATS:
            continue
        if m.get("fit_label") not in ("Good", "Marginal"):
            continue
        rows.append({
            "name": m.get("name", ""), "ollama_name": m["ollama_name"],
            "category": m.get("category", ""), "fit_label": m["fit_label"],
            "best_quant": m.get("best_quant", ""),
            "memory_gb": float(m.get("memory_required_gb") or 0.0),
            "tps": float(m.get("estimated_tps") or 0.0),
            "score": float(m.get("score") or 0.0),
            "use_case": m.get("use_case") or "",
            "params": m.get("parameter_count") or "",
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:_LLMFIT_MAX_ROWS]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py -v`
Expected: PASS (svi — uključivo stari; provjeri da hoist `run_isolated` nije slomio `_ip_mode` testove)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml ragspine/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): llmfit_models — llmfit kao izvor modela (dependencija llmfit>=1.1)"
```

---

### Task 2: page_model na llmfit retke (render + izbor + pull)

**Files:**
- Modify: `ragspine/ops/wizard.py` (`render_model_catalog` → `render_llmfit_models`; `page_model` prewiring)
- Test: `tests/test_wizard.py` (ažuriraj postojeće render/page_model testove)

**Interfaces:**
- Consumes: `preflight.llmfit_models(cfg)` (Task 1), sve ostalo postojeće (`ollama_ready/start_ollama/ollama_version/ollama_floor_ok/ollama_pull`, `setup_embedding`, `self_test`, `model_settings.save`, `tui`).
- Produces:
  - `_PILL_GLYPH = {"Good": "🟢", "Marginal": "🟡", "Too Tight": "🔴"}` (zamjena stare mape; `.get(..., "?")` fallback)
  - `render_llmfit_models(rows, *, out=print) -> list[str]` — ispiše retke (pill, ollama_name, params, best_quant, ~memory_gb GB, ~tps tok/s, use_case; ⭐ PREPORUKA na prvom retku), vrati listu `ollama_name` u prikazanom redoslijedu.
  - `page_model` — umjesto `model_fits`+`recommend_chat_model`: `rows = preflight.llmfit_models(cfg)`; `rows` None ili prazno → poruka + return True (postavi kasnije); izbor kroz `tui.prompt_choice` (default 0 = preporuka) + „Preskoči" opcija; pull `ollama_name`; ostatak (save, embedding, self-test) NEPROMIJENJEN.

- [ ] **Step 1: Write the failing test**

Zamijeni postojeći `test_render_model_catalog_marks_recommendation` i prilagodi `test_page_model_full_happy_path` (ostali page_model testovi — skip grana — ostaju, samo dodaj mock `llmfit_models` gdje treba):

```python
# tests/test_wizard.py
def _llmfit_rows():
    return [
        {"name": "hf/q7", "ollama_name": "qwen2.5:7b", "category": "Chat",
         "fit_label": "Good", "best_quant": "Q4_K_M", "memory_gb": 4.7,
         "tps": 11.0, "score": 90.0, "use_case": "opći asistent", "params": "7B"},
        {"name": "hf/l3", "ollama_name": "llama3.2:3b", "category": "Chat",
         "fit_label": "Marginal", "best_quant": "Q4_K_M", "memory_gb": 2.0,
         "tps": 20.0, "score": 70.0, "use_case": "brzi sažeci", "params": "3B"},
    ]


def test_render_llmfit_models_marks_first_as_recommendation():
    lines = []
    names = wizard.render_llmfit_models(_llmfit_rows(), out=lines.append)
    assert names == ["qwen2.5:7b", "llama3.2:3b"]
    assert any("PREPORUKA" in l for l in lines)
    assert any("🟢" in l for l in lines) and any("🟡" in l for l in lines)
    assert any("tok/s" in l for l in lines)


def test_page_model_skip_when_llmfit_unavailable(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (True, "radi"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url=None: "0.6.0")
    monkeypatch.setattr(wizard.preflight, "llmfit_models", lambda c=None: None)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "def-emb"
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader(), out=lambda *_: None)
    assert ok is True    # llmfit nedostupan -> postavi kasnije, ne zaglavi
```

U `test_page_model_full_happy_path`: zamijeni mock `system_state` mockom `llmfit_models` (vrati `_llmfit_rows()`); pull assertion sad očekuje `"qwen2.5:7b"` (prvi/preporuka, prazan input = default 0). Ostalo isto.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -k "render_llmfit or page_model" -v`
Expected: FAIL — `render_llmfit_models` ne postoji; happy-path zove nepostojeći tok

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/wizard.py — zamijeni _PILL_GLYPH i render_model_catalog:

_PILL_GLYPH = {"Good": "🟢", "Marginal": "🟡", "Too Tight": "🔴"}


def render_llmfit_models(rows, *, out=print) -> list[str]:
    """Ispiši llmfit retke (već filtrirane i sortirane po score-u); vrati
    ollama imena u prikazanom redoslijedu. Prvi = ⭐ preporuka."""
    names = []
    for i, r in enumerate(rows):
        pill = _PILL_GLYPH.get(r["fit_label"], "?")
        star = "  ⭐ PREPORUKA" if i == 0 else ""
        out(f"  {pill} {r['ollama_name']} ({r['params']}, {r['best_quant']} "
            f"~{r['memory_gb']:.1f} GB, ~{r['tps']:.0f} tok/s) — {r['use_case']}{star}")
        names.append(r["ollama_name"])
    return names
```

U `page_model` zamijeni blok kataloga (od `fits = preflight.model_fits(cfg)` do `model = names[idx]`):

```python
    rows = preflight.llmfit_models(cfg)
    if not rows:
        out("llmfit nije dostupan ili nema modela koji stanu — model postavi kasnije u Postavkama.")
        return True
    out("Modeli za ovaj hardver (llmfit — kvantizacija izračunata po stroju):")
    names = render_llmfit_models(rows, out=out)
    choices = names + ["Preskoči — postavi kasnije"]
    idx = tui.prompt_choice("Odaberi JEDAN model:", choices, default=0,
                            input_fn=input_fn, out=out)
    if idx == len(names):
        return True
    model = names[idx]
```

Ostatak funkcije (pull, save, embedding, self-test) NE diraj.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py -v`
Expected: PASS (svi; stari render_model_catalog test zamijenjen)

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): stranica 3 čita llmfit — render + izbor + pull po ollama_name"
```

---

### Task 3: Brisanje kataloga + čišćenje + puni suite

**Files:**
- Modify: `ragspine/ops/preflight.py` (obriši `MODEL_CATALOG`, `model_fits`, `recommend_chat_model`, `_params_b`; `fit_pill` i pragovi `_FITS_FRAC`/`_TIGHT_FRAC`/`_VRAM_RESERVE` OSTAJU — `fit_pill` koristi `choose_embed_model`, a `_VRAM_RESERVE`/ostalo provjeri: ako nakon brisanja nešto od toga nitko ne koristi, obriši i to)
- Modify: `tests/test_preflight.py` (obriši testove kataloga: `test_catalog_has_desc_and_new_models`, `test_model_fits_carries_desc`, `test_recommend_chat_model_*`; testove za `fit_pill` zadrži ako postoje)
- Modify: `ragspine/ops/wizard.py:13` (komentar `_BGE_M3_GB` referira MODEL_CATALOG — preformuliraj)
- Test: postojeći

**Interfaces:**
- Consumes: ništa novo. Produces: ništa novo — čisto brisanje.

- [ ] **Step 1: Obriši kod i testove**

`git grep -n "model_fits\|MODEL_CATALOG\|recommend_chat_model\|_params_b" ragspine tests` — nakon brisanja mora vratiti NULA pogodaka (osim možda povijesnih komentara — i njih počisti). Poznati potrošači prije brisanja: samo `wizard.py` (riješeno u Tasku 2) i testovi kataloga.

- [ ] **Step 2: Ciljani testovi**

Run: `pytest tests/test_preflight.py tests/test_wizard.py -v`
Expected: PASS

- [ ] **Step 3: Puni suite + cyrillic gate**

Run: `python -m pytest -q`
Expected: zeleno (bez novih padova)

- [ ] **Step 4: Commit**

```bash
git add ragspine/ops/preflight.py ragspine/ops/wizard.py tests/test_preflight.py
git commit -m "refactor(preflight): MODEL_CATALOG/model_fits van — llmfit je izvor istine"
```

---

## Self-Review (autor)

**Spec coverage (korisnikova odluka + spec str.3):**
- llmfit izvor, ragspine prikaz (pill/opis/brzina/⭐) → Task 1, 2. ✓
- Kompresija: llmfitov `best_quant`/`memory_required_gb` po stvarnom hardveru — prikazano u retku. ✓
- llmfit dolazi kao dependencija (pip wheel, MIT) → Task 1 pyproject. ✓ (postojeće instalacije: `pip install -U ragspine` povuče llmfit; air-gapped: llmfit instaliran zajedno s ragspineom, `--json` radi offline)
- Katalog obrisan → Task 3; `fit_pill` ostaje samo za embedding (`choose_embed_model`). ✓
- „Ne zaglavi": llmfit nedostupan → skip grana (Task 2 test). ✓
- `setup.llmfit()` (stari poziv u `ops/setup.py`) NE diramo — vlastiti potrošač (`run(cfg)` sažetak), radi i dalje.

**Placeholder scan:** čisto.

**Type consistency:** `llmfit_models` red-oblik (`ollama_name/fit_label/best_quant/memory_gb/tps/score/use_case/params`) konzistentan Task 1 (produces) ↔ Task 2 (consumes u render/page_model testovima i kodu). `_PILL_GLYPH` ključevi = llmfit `fit_label` vrijednosti (izmjereno: Good/Marginal/Too Tight).

## Runnable check

`pytest tests/test_preflight.py tests/test_wizard.py tests/test_no_cyrillic.py -q` zelen + `python -m pytest -q` zelen + ručno: `python -m llmfit --json | head -c 200` vrati JSON.
