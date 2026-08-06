# Setup Wizard P2 — Stranica 3 (Model/LLM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stranica 3 setup wizarda — Ollama spremnost (verzija+auto-start), bogat katalog s fit-pillovima i preporukom, izbor JEDNOG modela, download s progresom, embedding s fallbackom, self-test gate; `mark_complete` se pomiče iza stranice 3.

**Architecture:** Sva infrastruktura postoji (P1 + ranije): `preflight.MODEL_CATALOG/model_fits/fit_pill/ollama_ready`, `model_settings.save/apply/test_connection`, `embed.download_model`, `tui.*`, `wizard_state.*`. P2 proširuje `preflight.py` (verzija, auto-start, katalog, preporuka, pull), dodaje `page_model` u `wizard.py` i pomiče `mark_complete` iza stagea 3. Ollama nedostupna → grana „preskoči, postavi kasnije" (ne zaglavi). Kvar self-testa NE poništava setup.

**Tech Stack:** Python 3.11+ stdlib (urllib, subprocess, json, re), postojeći moduli. Bez novih dependencija.

**Scope note:** Winget auto-install (spec str.1) ide u P3 (uz ostali Windows-elevation posao: netsh, servisni račun, ACL). Proxy polje ide u P3 (koristi ga Ollama daemon, ne mi — treba servisna env konfiguracija iz str.4).

## Global Constraints

- Jezik koda/komentara/UI stringova: hrvatski (latinica). Cyrillic-gate `tests/test_no_cyrillic.py` mora ostati zelen (emoji 🟢🟡🔴✓⚠✗ su simboli, ne ćirilica — smiju).
- Python floor: 3.11+.
- Bez novih dependencija (stdlib only).
- CI zelen na 4 posla (ubuntu 3.11/3.13, macos 3.13, windows 3.11).
- TUI/wizard funkcije primaju injektabilni `input_fn`/`out`; testovi bez pravog stdina i BEZ mreže (mockaj `ollama_ready`, `ollama_version`, `ollama_pull`, `internet_ok`, LLM pozive — `tests/test_preflight.py` već ima autouse `_no_live_network` fixture kao uzor).
- Setup-stanje u `config_overrides(module='setup', ...)` preko `wizard_state`; odabir modela u `config_overrides(module='model', ...)` preko `business/model_settings.save` — NE novi mehanizam.
- `wizard_state.mark_complete` se poziva TOČNO JEDNOM u `run()`, iza zadnje implementirane stranice (P2 = iza stagea 3). Skip stranice 3 svejedno postavlja stage/complete (spec: „ne zaglavi").
- Ollama version floor: **0.5.0**.
- Self-test: uspjeh = ne-prazan odgovor unutar timeouta; regex `OK RAGSPINE` (case-insensitive) = soft-check (upozorenje, ne fail); 3 pokušaja; Preskoči nakon neuspjeha; kvar ne ruši setup.

---

### Task 1: Ollama verzija (floor 0.5.0) + auto-start servisa

**Files:**
- Modify: `ragspine/ops/preflight.py` (uz postojeći `ollama_ready`, oko retka 129)
- Test: `tests/test_preflight.py` (dodaj)

**Interfaces:**
- Consumes: postojeći `ollama_ready(url) -> tuple[bool, str]` (preflight.py).
- Produces:
  - `ollama_version(url="http://127.0.0.1:11434") -> str | None` (GET `/api/version`, JSON polje `version`; None na grešku)
  - `ollama_floor_ok(version: str | None, floor: str = "0.5.0") -> bool` (tuple-usporedba; None/neparsabilno → False)
  - `start_ollama(wait_s: float = 8.0, url: str = "http://127.0.0.1:11434") -> bool` (pokuša `ollama serve` detached; polla `ollama_ready` do wait_s; True kad proradi)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj; autouse _no_live_network fixture vec postoji u ovom fajlu)
def test_ollama_version_parses(monkeypatch):
    import io, json

    class _Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(pf.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(json.dumps({"version": "0.5.4"}).encode()))
    assert pf.ollama_version("http://x") == "0.5.4"


def test_ollama_version_none_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("nema servisa")
    monkeypatch.setattr(pf.urllib.request, "urlopen", _boom)
    assert pf.ollama_version("http://x") is None


def test_ollama_floor_ok():
    assert pf.ollama_floor_ok("0.5.0") is True
    assert pf.ollama_floor_ok("0.12.1") is True
    assert pf.ollama_floor_ok("0.4.9") is False
    assert pf.ollama_floor_ok(None) is False
    assert pf.ollama_floor_ok("čudno") is False


def test_start_ollama_returns_true_when_service_comes_up(monkeypatch):
    monkeypatch.setattr(pf.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(pf, "ollama_ready", lambda url=None: (True, "servis radi"))
    assert pf.start_ollama(wait_s=0.1) is True


def test_start_ollama_false_when_binary_missing(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("ollama")
    monkeypatch.setattr(pf.subprocess, "Popen", _boom)
    assert pf.start_ollama(wait_s=0.1) is False
```

Napomena: `preflight.py` trenutno importa `urllib.request` lokalno unutar `ollama_ready` — Step 3 podiže `urllib.request`, `subprocess`, `time` na module-level da bi monkeypatch preko `pf.urllib.request` radio.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k "ollama_version or ollama_floor or start_ollama" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'ollama_version'`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/preflight.py — module-level importi (vrh fajla, uz postojeće):
import subprocess
import time
import urllib.request

# ragspine/ops/preflight.py — uz ollama_ready (postojeći lokalni
# "import urllib.request" unutar ollama_ready ukloni — sad je module-level):

_OLLAMA_FLOOR = "0.5.0"


def ollama_version(url: str = "http://127.0.0.1:11434") -> str | None:
    """GET /api/version -> "0.5.4" ili None. Ne baca."""
    import json
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=3) as r:
            return json.loads(r.read()).get("version") or None
    except Exception:
        return None


def ollama_floor_ok(version: str | None, floor: str = _OLLAMA_FLOOR) -> bool:
    """Tuple-usporedba "0.5.4" >= "0.5.0". Neparsabilno/None -> False."""
    if not version:
        return False
    try:
        v = tuple(int(x) for x in version.split("-")[0].split("."))
        f = tuple(int(x) for x in floor.split("."))
        return v >= f
    except ValueError:
        return False


def start_ollama(wait_s: float = 8.0, url: str = "http://127.0.0.1:11434") -> bool:
    """Pokusaj pokrenuti `ollama serve` detached pa cekaj da /api/tags prodise.
    False kad binary ne postoji ili servis ne prodise u wait_s."""
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        ok, _ = ollama_ready(url)
        if ok:
            return True
        time.sleep(0.25)
    ok, _ = ollama_ready(url)
    return ok
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py -v`
Expected: PASS (svi, uključivo stari — provjeri da uklanjanje lokalnog importa iz `ollama_ready` nije ništa slomilo)

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): ollama verzija (floor 0.5.0) + auto-start servisa"
```

---

### Task 2: MODEL_CATALOG proširenje + opisi + preporuka

**Files:**
- Modify: `ragspine/ops/preflight.py:248-265` (`MODEL_CATALOG`), iza `model_fits` dodaj `recommend_chat_model`
- Test: `tests/test_preflight.py` (dodaj)

**Interfaces:**
- Consumes: `model_fits(cfg, state)` (postojeći; prosljeđuje nova polja jer gradi izlaz iz `MODEL_CATALOG` — provjeri: izlaz `model_fits` NE kopira `desc`, pa ga dodaj u izlazni dict).
- Produces:
  - `MODEL_CATALOG` prošireni: svaki unos dobiva `desc: str` („za što je dobar"); novi chat modeli: `mistral:7b`, `gemma2:9b`, `phi4:14b`, `deepseek-r1:7b`, `deepseek-r1:14b`, `qwen2.5-coder:7b`.
  - `model_fits` izlaz dobiva ključ `desc`.
  - `recommend_chat_model(fits: list[dict]) -> str | None` — ime najvećeg chat modela koji KOMOTNO stane (best_quant), fallback najveći tight; None kad ništa ne stane.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj)
def test_catalog_has_desc_and_new_models():
    names = {m["name"] for m in pf.MODEL_CATALOG}
    assert {"mistral:7b", "gemma2:9b", "phi4:14b", "deepseek-r1:7b",
            "deepseek-r1:14b", "qwen2.5-coder:7b"} <= names
    assert all(m.get("desc") for m in pf.MODEL_CATALOG)


def test_model_fits_carries_desc():
    fits = pf.model_fits(state={"ram_total_gb": 16.0, "vram_gb": 0.0})
    assert all("desc" in f for f in fits)


def test_recommend_chat_model_prefers_largest_fitting():
    fits = pf.model_fits(state={"ram_total_gb": 16.0, "vram_gb": 0.0})
    rec = pf.recommend_chat_model(fits)
    # 16 GB: 7-8B Q4/Q5 komotno stanu, 14B Q4 (9.0) je tight (>50%), 32B ne
    assert rec is not None
    chat = {f["name"]: f for f in fits if f["role"] == "chat"}
    assert chat[rec]["best_quant"] is not None


def test_recommend_chat_model_none_when_nothing_fits():
    fits = pf.model_fits(state={"ram_total_gb": 1.0, "vram_gb": 0.0})
    assert pf.recommend_chat_model(fits) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k "catalog_has_desc or carries_desc or recommend_chat" -v`
Expected: FAIL — novi modeli/desc ne postoje; `recommend_chat_model` ne postoji

- [ ] **Step 3: Write minimal implementation**

`MODEL_CATALOG` — svakom POSTOJEĆEM unosu dodaj `desc`, i ubaci nove (veličine GB približne, kao i dosad):

```python
MODEL_CATALOG = [
    {"name": "qwen2.5:3b", "role": "chat", "params": "3B",
     "desc": "brz opci asistent za slabija racunala; solidan hrvatski",
     "quants": {"Q4_K_M": 2.0, "Q5_K_M": 2.3, "Q8_0": 3.3, "fp16": 6.2}},
    {"name": "llama3.2:3b", "role": "chat", "params": "3B",
     "desc": "lagani Meta model; dobar za sazetke i jednostavna pitanja",
     "quants": {"Q4_K_M": 2.0, "Q5_K_M": 2.3, "Q8_0": 3.4, "fp16": 6.4}},
    {"name": "mistral:7b", "role": "chat", "params": "7B",
     "desc": "brz i precizan generalist; dobro slijedi upute",
     "quants": {"Q4_K_M": 4.4, "Q5_K_M": 5.1, "Q8_0": 7.7, "fp16": 14.5}},
    {"name": "qwen2.5:7b", "role": "chat", "params": "7B",
     "desc": "najbolji omjer kvalitete i brzine; preporuka za vecinu ureda",
     "quants": {"Q4_K_M": 4.7, "Q5_K_M": 5.4, "Q8_0": 8.1, "fp16": 15.2}},
    {"name": "qwen2.5-coder:7b", "role": "chat", "params": "7B",
     "desc": "specijaliziran za kod i strukturirane formate (SQL, JSON)",
     "quants": {"Q4_K_M": 4.7, "Q5_K_M": 5.4, "Q8_0": 8.1, "fp16": 15.2}},
    {"name": "deepseek-r1:7b", "role": "chat", "params": "7B",
     "desc": "rezonira korak-po-korak; sporiji, bolji na racunskim zadacima",
     "quants": {"Q4_K_M": 4.7, "Q5_K_M": 5.4, "Q8_0": 8.1, "fp16": 15.2}},
    {"name": "llama3.1:8b", "role": "chat", "params": "8B",
     "desc": "prokusani Meta generalist; siroko testiran",
     "quants": {"Q4_K_M": 4.9, "Q5_K_M": 5.7, "Q8_0": 8.5, "fp16": 16.1}},
    {"name": "gemma2:9b", "role": "chat", "params": "9B",
     "desc": "Google model; jak na razumijevanju teksta i sazimanju",
     "quants": {"Q4_K_M": 5.8, "Q5_K_M": 6.6, "Q8_0": 9.8, "fp16": 18.5}},
    {"name": "qwen2.5:14b", "role": "chat", "params": "14B",
     "desc": "osjetno pametniji od 7B; treba 16+ GB RAM-a",
     "quants": {"Q4_K_M": 9.0, "Q5_K_M": 10.5, "Q8_0": 15.7, "fp16": 29.5}},
    {"name": "deepseek-r1:14b", "role": "chat", "params": "14B",
     "desc": "jace rezoniranje za slozene obracune; sporiji odziv",
     "quants": {"Q4_K_M": 9.0, "Q5_K_M": 10.5, "Q8_0": 15.7, "fp16": 29.5}},
    {"name": "phi4:14b", "role": "chat", "params": "14B",
     "desc": "Microsoftov kompaktni 14B; jak na logici i matematici",
     "quants": {"Q4_K_M": 9.1, "Q5_K_M": 10.6, "Q8_0": 15.8, "fp16": 29.3}},
    {"name": "qwen2.5:32b", "role": "chat", "params": "32B",
     "desc": "najjaci lokalni izbor; samo za servere s 64+ GB RAM-a",
     "quants": {"Q4_K_M": 19.9, "Q5_K_M": 23.3, "Q8_0": 34.8, "fp16": 65.5}},
    {"name": "bge-m3", "role": "embed", "params": "0.6B",
     "desc": "visejezicni embedding (i hrvatski); bolji retrieval",
     "quants": {"Q4_K_M": 0.4, "fp16": 1.2}},
    {"name": "nomic-embed-text", "role": "embed", "params": "0.1B",
     "desc": "mali embedding za slabija racunala",
     "quants": {"Q4_K_M": 0.1, "fp16": 0.3}},
]
```

U `model_fits` izlaznom dictu (preflight.py:308-310) dodaj `"desc": m.get("desc", "")`:

```python
        out.append({"name": m["name"], "role": m["role"], "params": m["params"],
                    "desc": m.get("desc", ""),
                    "quants": quants, "best_quant": best_fit, "tight_quant": tight_fit,
                    "installable": best_fit is not None or tight_fit is not None})
```

Iza `model_fits` dodaj:

```python
def _params_b(params: str) -> float:
    """"7B" -> 7.0; neparsabilno -> 0."""
    try:
        return float(params.rstrip("Bb"))
    except ValueError:
        return 0.0


def recommend_chat_model(fits: list[dict]) -> str | None:
    """Najveci chat model koji KOMOTNO stane (best_quant); fallback najveci
    koji barem tijesno stane. None kad nista ne stane."""
    chat = [f for f in fits if f["role"] == "chat"]
    comfy = [f for f in chat if f["best_quant"]]
    if comfy:
        return max(comfy, key=lambda f: _params_b(f["params"]))["name"]
    tight = [f for f in chat if f["tight_quant"]]
    if tight:
        return max(tight, key=lambda f: _params_b(f["params"]))["name"]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py tests/test_model_recommender.py tests/test_model_settings.py -v`
Expected: PASS. `test_model_recommender.py` koristi vlastitu logiku, ali ako neki njegov test asertira točan sastav/duljinu `MODEL_CATALOG`, ažuriraj taj test na novi katalog (to je dio ovog taska).

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): bogatiji MODEL_CATALOG (opisi, novi modeli) + recommend_chat_model"
```

---

### Task 3: Ollama pull s progresom (stdlib streaming)

**Files:**
- Modify: `ragspine/ops/preflight.py` (iza `start_ollama`)
- Test: `tests/test_preflight.py` (dodaj)

**Interfaces:**
- Produces: `ollama_pull(name: str, url: str = "http://127.0.0.1:11434", *, out=print) -> bool` — POST `{url}/api/pull` s `{"model": name}`, čita NDJSON stream, ispisuje napredak (status + postotak kad ima `total`/`completed`), True na završni `{"status":"success"}`. Ollama daemon sam radi resume djelomičnog downloada — mi samo ponovno pozovemo.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj)
def _ndjson_resp(lines):
    import io, json
    payload = b"".join(json.dumps(l).encode() + b"\n" for l in lines)

    class _Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _Resp(payload)


def test_ollama_pull_success_with_progress(monkeypatch):
    resp = _ndjson_resp([
        {"status": "pulling manifest"},
        {"status": "downloading", "total": 100, "completed": 50},
        {"status": "success"},
    ])
    monkeypatch.setattr(pf.urllib.request, "urlopen", lambda *a, **k: resp)
    lines = []
    assert pf.ollama_pull("qwen2.5:7b", "http://x", out=lines.append) is True
    assert any("50%" in l for l in lines)


def test_ollama_pull_false_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("mreza pukla")
    monkeypatch.setattr(pf.urllib.request, "urlopen", _boom)
    lines = []
    assert pf.ollama_pull("qwen2.5:7b", "http://x", out=lines.append) is False
    assert any("Gre" in l for l in lines)   # "Greska..." poruka, ne traceback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k ollama_pull -v`
Expected: FAIL — `ollama_pull` ne postoji

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/preflight.py  (iza start_ollama; json vec importan lokalno drugdje —
# koristi lokalni import kao ollama_version)

def ollama_pull(name: str, url: str = "http://127.0.0.1:11434", *, out=print) -> bool:
    """Skini model preko Ollama daemona (POST /api/pull, NDJSON stream).
    Daemon sam nastavlja djelomicni download (resume) — dovoljno je ponovno pozvati.
    True na zavrsni status "success"."""
    import json
    req = urllib.request.Request(f"{url}/api/pull",
                                 data=json.dumps({"model": name}).encode(),
                                 headers={"Content-Type": "application/json"})
    last_pct = -1
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            for raw in r:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("error"):
                    out(f"Greska pri skidanju: {ev['error']}")
                    return False
                status = ev.get("status", "")
                total, done = ev.get("total"), ev.get("completed")
                if total and done is not None:
                    pct = int(done * 100 / total)
                    if pct != last_pct:   # ne spamaj isti postotak
                        out(f"  {status}: {pct}%")
                        last_pct = pct
                elif status:
                    out(f"  {status}")
                if status == "success":
                    return True
    except Exception as e:
        out(f"Greska pri skidanju modela: {e}")
        return False
    out("Skidanje prekinuto prije kraja — pokreni ponovno (nastavlja gdje je stalo).")
    return False
```

Napomena: `timeout=30` je socket-timeout po čitanju (stream se osvježava svakim eventom), ne ukupni limit downloada.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): ollama_pull NDJSON stream s postotkom (resume radi daemon)"
```

---

### Task 4: Embedding — izbor s fit-pillom + download+verify + fallback

**Files:**
- Modify: `ragspine/ops/wizard.py` (nove pomoćne funkcije, prije `run`)
- Test: `tests/test_wizard.py` (dodaj)

**Interfaces:**
- Consumes: `preflight.fit_pill(size_gb, total_gb)`; `ragspine/rag/embed.py download_model(cfg) -> {ok, model, dim} | {ok: False, error}`; `cfg.embed_model` (default `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`); `dataclasses.replace`.
- Produces:
  - `choose_embed_model(state: dict, default_model: str) -> str` — `"BAAI/bge-m3"` kad fp16 (1.2 GB) KOMOTNO stane u ukupni RAM (`fit_pill == "fits"`), inače `default_model`.
  - `setup_embedding(spine, cfg, *, out=print) -> bool` — odabere, skine+verificira (`embed.download_model`), na grešku fallback na `cfg.embed_model`; uspješan odabir spremi preko `model_settings` ključa `embed_model` (vidi Task 6 — `page_model` zove `model_settings.save` jednom sa svime; `setup_embedding` NE sprema sam, samo vraća odabir kroz povratnu vrijednost — vidi dolje).

Radi jednostavnosti sučelja: `setup_embedding` vraća `str | None` — ime verificiranog embedding modela (None = ni fallback nije prošao; ne blokira setup, samo upozori).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard.py  (dodaj)
def test_choose_embed_model_bge_when_ram_allows():
    st = {"ram_total_gb": 16.0}
    assert wizard.choose_embed_model(st, "mali-default") == "BAAI/bge-m3"


def test_choose_embed_model_fallback_on_small_ram():
    st = {"ram_total_gb": 2.0}   # 1.2/2.0 = 60% -> tight, ne "fits"
    assert wizard.choose_embed_model(st, "mali-default") == "mali-default"


def test_setup_embedding_falls_back_on_download_error(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    from ragspine.config import Config
    s = init_spine(str(tmp_path / "t.db"))
    cfg = Config(data_dir=str(tmp_path))
    calls = []

    def _fake_download(c):
        calls.append(c.embed_model)
        if c.embed_model == "BAAI/bge-m3":
            return {"ok": False, "error": "ne stane"}
        return {"ok": True, "model": c.embed_model, "dim": 384}

    monkeypatch.setattr(wizard, "_download_embed", _fake_download)
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ram_total_gb": 16.0})
    got = wizard.setup_embedding(s, cfg, out=lambda *_: None)
    assert got == cfg.embed_model          # fallback na default
    assert calls == ["BAAI/bge-m3", cfg.embed_model]
```

Napomena: ako `Config(data_dir=...)` nije stvarni konstruktor-potpis (provjeri `ragspine/config.py` — Config je dataclass), konstruiraj cfg kako postojeći testovi u `tests/test_wizard.py`/`tests/conftest.py` već rade (postoji `cfg` fixture) i koristi taj obrazac.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -k embed -v`
Expected: FAIL — `choose_embed_model` ne postoji

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/wizard.py  (novi importi na vrh)
import dataclasses

_BGE_M3 = "BAAI/bge-m3"
_BGE_M3_GB = 1.2   # fp16, priblizno (kao MODEL_CATALOG)


def _download_embed(cfg):
    """Indirekcija radi testabilnosti (embed vuce fastembed tek pri pozivu)."""
    from ragspine.rag import embed
    return embed.download_model(cfg)


def choose_embed_model(state: dict, default_model: str) -> str:
    """bge-m3 kad KOMOTNO stane u ukupni RAM; inace ostavi default (mali)."""
    total = state.get("ram_total_gb") or 0.0
    if preflight.fit_pill(_BGE_M3_GB, total) == "fits":
        return _BGE_M3
    return default_model


def setup_embedding(spine, cfg, *, out=print) -> str | None:
    """Odaberi embedding po RAM-u, skini i VERIFICIRAJ; na gresku fallback na
    cfg.embed_model. Vrati ime verificiranog modela ili None (ne blokira setup)."""
    chosen = choose_embed_model(preflight.system_state(cfg), cfg.embed_model)
    for candidate in dict.fromkeys([chosen, cfg.embed_model]):   # bez duplikata
        out(f"Embedding model: {candidate} — skidam i provjeravam...")
        res = _download_embed(dataclasses.replace(cfg, embed_model=candidate))
        if res.get("ok"):
            out(f"  ✓ {candidate} (dim {res.get('dim')})")
            return candidate
        out(f"  ⚠ {candidate}: {res.get('error', 'nepoznata greska')}")
    out("Embedding nije skinut — RAG indeksiranje nece raditi dok se ne skine u Postavkama.")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): embedding izbor po RAM-u + download/verify s fallbackom"
```

---

### Task 5: Self-test gate (cold-load svjestan, 3 pokušaja, soft regex)

**Files:**
- Modify: `ragspine/ops/wizard.py`
- Test: `tests/test_wizard.py` (dodaj)

**Interfaces:**
- Consumes: `business/model_settings.apply(spine, cfg)`, `core/llm.LLMClient(cfg).complete(messages, max_tokens=...)` (timeout 120 s u llm.py — pokriva cold-load 7B), iznimke `LLMError`/`LLMUnavailable`.
- Produces: `self_test(spine, cfg, *, input_fn=input, out=print, retries=3) -> bool` — šalje `"Odgovori točno: OK RAGSPINE"`; uspjeh = ne-prazan odgovor (timeout rješava LLMClient); regex `OK RAGSPINE` case-insensitive = soft-check (samo upozorenje); do `retries` pokušaja s pitanjem „Pokušaj ponovno?" između; False tek kad korisnik odustane. Kvar NE poništava setup (poziva ga `page_model`, koji svejedno vraća True).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard.py  (dodaj)
class _FakeRes:
    def __init__(self, text):
        self.text = text
        self.model = "test-model"


def test_self_test_ok_on_nonempty_answer(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "_llm_complete", lambda spine, cfg, prompt: _FakeRes("OK RAGSPINE"))
    lines = []
    assert wizard.self_test(s, None, input_fn=_reader(), out=lines.append) is True
    assert not any("upozorenje" in l.lower() for l in lines)


def test_self_test_soft_warns_on_wrong_text(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "_llm_complete", lambda spine, cfg, prompt: _FakeRes("bok!"))
    lines = []
    assert wizard.self_test(s, None, input_fn=_reader(), out=lines.append) is True
    assert any("upozorenje" in l.lower() for l in lines)


def test_self_test_retries_then_user_gives_up(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    calls = []

    def _fail(spine, cfg, prompt):
        calls.append(1)
        raise wizard.LLMUnavailable("hladno")

    monkeypatch.setattr(wizard, "_llm_complete", _fail)
    # dva puta "da, pokusaj ponovno", pa "ne" -> False; ukupno 3 poziva LLM-a
    ok = wizard.self_test(s, None, input_fn=_reader("da", "da", "ne"),
                          out=lambda *_: None)
    assert ok is False
    assert len(calls) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -k self_test -v`
Expected: FAIL — `self_test`/`_llm_complete`/`LLMUnavailable` nisu u modulu

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/wizard.py  (novi importi na vrh)
import re
import time
from ragspine.core.llm import LLMError, LLMUnavailable

_SELF_TEST_PROMPT = "Odgovori točno: OK RAGSPINE"


def _llm_complete(spine, cfg, prompt: str):
    """Indirekcija radi testabilnosti; LLMClient ima vlastiti timeout (120 s)
    koji pokriva i cold-load vecih modela."""
    from ragspine.business import model_settings
    from ragspine.core.llm import LLMClient
    return LLMClient(model_settings.apply(spine, cfg)).complete(
        [{"role": "user", "content": prompt}], max_tokens=20)


def self_test(spine, cfg, *, input_fn=input, out=print, retries: int = 3) -> bool:
    """Kratki test odabranog modela. Uspjeh = ne-prazan odgovor unutar timeouta.
    Regex "OK RAGSPINE" = soft-check (upozorenje). Kvar ne rusi setup."""
    for attempt in range(1, retries + 1):
        out(f"Self-test modela (pokusaj {attempt}/{retries}; prvi odziv zna trajati i minutu)...")
        t0 = time.monotonic()
        try:
            res = _llm_complete(spine, cfg, _SELF_TEST_PROMPT)
        except (LLMError, LLMUnavailable, Exception) as e:
            out(f"  ✗ {e}")
            if attempt < retries and tui.prompt_yes_no(
                    "Pokusaj ponovno?", default=True, input_fn=input_fn, out=out):
                continue
            return False
        text = (getattr(res, "text", "") or "").strip()
        if not text:
            out("  ✗ prazan odgovor")
            if attempt < retries and tui.prompt_yes_no(
                    "Pokusaj ponovno?", default=True, input_fn=input_fn, out=out):
                continue
            return False
        elapsed = time.monotonic() - t0
        out(f"  ✓ model odgovara ({elapsed:.1f} s)")
        if not re.search(r"OK RAGSPINE", text, re.IGNORECASE):
            out("  ⚠ upozorenje: odgovor ne sadrzi 'OK RAGSPINE' — model radi, ali slabo slijedi upute.")
        return True
    return False
```

Pazi na petlju: nakon zadnjeg (`attempt == retries`) neuspjeha NE pitaj „Pokušaj ponovno?" — odmah False (test iznad očekuje točno 3 LLM poziva uz odgovore „da, da, ne": provjeri logiku — 1. fail → pita → da; 2. fail → pita → da; 3. fail → `attempt == retries` → vrati False bez pitanja. Odgovor „ne" iz testa tada ostaje nepotrošen — u redu, `_reader` je lijen. Ako implementiraš drukčije, uskladi test i navedi u reportu).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): self-test gate — ne-prazan odgovor, soft 'OK RAGSPINE', 3 pokusaja"
```

---

### Task 6: Stranica 3 — page_model (katalog, izbor, pull, spremanje)

**Files:**
- Modify: `ragspine/ops/wizard.py`
- Test: `tests/test_wizard.py` (dodaj)

**Interfaces:**
- Consumes: Task 1 (`preflight.ollama_ready/ollama_version/ollama_floor_ok/start_ollama`), Task 2 (`preflight.model_fits/recommend_chat_model`), Task 3 (`preflight.ollama_pull`), Task 4 (`setup_embedding`), Task 5 (`self_test`), `business/model_settings.save`, `tui.prompt_choice/prompt_yes_no/print_header`.
- Produces:
  - `_PILL_GLYPH = {"fits": "🟢", "tight": "🟡", "too_big": "🔴", "unknown": "?"}`
  - `render_model_catalog(fits, recommended, *, out=print) -> list[str]` — ispiše instalabilne chat modele (pill najbolje kvantizacije, params, desc, `[GPU]` kad `gpu_ready`, `⭐ PREPORUKA` na preporučenom); vrati imena u prikazanom redoslijedu.
  - `page_model(spine, cfg, *, input_fn=input, out=print) -> bool` — Ollama spremnost (auto-start, verzija-floor upozorenje) → grana „preskoči, postavi kasnije" → izbor JEDNOG → pull → `model_settings.save(spine, "ollama", model=<ime>, ollama_url=cfg.ollama_url, user="setup")` → embedding (`setup_embedding`; rezultat u `save` pozivu kroz `embed_model=`) → `self_test` (kvar samo upozori). Vraća False SAMO kad korisnik odustane usred obaveznog koraka; skip-grana vraća True.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard.py  (dodaj)
def _fits_16gb():
    return wizard.preflight.model_fits(state={"ram_total_gb": 16.0, "vram_gb": 0.0})


def test_render_model_catalog_marks_recommendation():
    fits = _fits_16gb()
    rec = wizard.preflight.recommend_chat_model(fits)
    lines = []
    names = wizard.render_model_catalog(fits, rec, out=lines.append)
    assert rec in names
    assert any("PREPORUKA" in l for l in lines)
    assert any("🟢" in l or "🟡" in l for l in lines)
    assert all(wizard.preflight.MODEL_CATALOG, )  # placeholder-guard: vidi Step 3


def test_page_model_skip_branch_when_ollama_unavailable(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (False, "nema"))
    monkeypatch.setattr(wizard.preflight, "start_ollama", lambda **k: False)
    # "da" na "preskoci i postavi kasnije?"
    ok = wizard.page_model(s, None, input_fn=_reader("da"), out=lambda *_: None)
    assert ok is True


def test_page_model_full_happy_path(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    from ragspine.business import model_settings
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url=None: (True, "radi"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url=None: "0.6.0")
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ram_total_gb": 16.0, "vram_gb": 0.0})
    pulled = []
    monkeypatch.setattr(wizard.preflight, "ollama_pull",
                        lambda name, url=None, out=print: pulled.append(name) or True)
    monkeypatch.setattr(wizard, "setup_embedding", lambda sp, c, out=print: "emb-model")
    monkeypatch.setattr(wizard, "self_test", lambda sp, c, **k: True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "def-emb"
    # "" = prihvati default (preporuceni model je predodabran u prompt_choice)
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader(""), out=lambda *_: None)
    assert ok is True
    assert len(pulled) == 1
    saved = model_settings.get(s)
    assert saved["provider"] == "ollama"
    assert saved["model"] == pulled[0]
    assert saved["embed_model"] == "emb-model"
```

Prvi test sadrži namjerno pokvaren redak `assert all(wizard.preflight.MODEL_CATALOG, )` — UKLONI ga pri pisanju (ostatak testa je pravi sadržaj); plan ga označava da se ne prepiše slijepo.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -k "render_model or page_model" -v`
Expected: FAIL — `render_model_catalog`/`page_model` ne postoje

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/wizard.py

_PILL_GLYPH = {"fits": "🟢", "tight": "🟡", "too_big": "🔴", "unknown": "?"}


def render_model_catalog(fits, recommended, *, out=print) -> list[str]:
    """Ispisi instalabilne chat modele; vrati imena u prikazanom redoslijedu."""
    names = []
    for f in fits:
        if f["role"] != "chat" or not f["installable"]:
            continue
        q = f["best_quant"] or f["tight_quant"]
        qinfo = next(x for x in f["quants"] if x["quant"] == q)
        pill = _PILL_GLYPH.get(qinfo["pill"], "?")
        gpu = " [GPU]" if qinfo["gpu_ready"] else ""
        star = "  ⭐ PREPORUKA" if f["name"] == recommended else ""
        out(f"  {pill} {f['name']} ({f['params']}, {q} ~{qinfo['size_gb']} GB){gpu} — {f['desc']}{star}")
        names.append(f["name"])
    return names


def page_model(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 3: Ollama spremnost -> katalog -> JEDAN model -> pull -> spremi
    -> embedding -> self-test. Skip-grana vraca True (spec: ne zaglavi)."""
    tui.print_header("3/6  Model (LLM)", out=out)
    url = getattr(cfg, "ollama_url", "http://127.0.0.1:11434")

    ok, detail = preflight.ollama_ready(url)
    if not ok:
        out(f"Ollama: {detail} — pokusavam pokrenuti servis...")
        ok = preflight.start_ollama(url=url)
    if not ok:
        out("Ollama nije dostupna. Model mozes postaviti kasnije u Postavkama.")
        if tui.prompt_yes_no("Preskoci stranicu modela?", default=True,
                             input_fn=input_fn, out=out):
            return True
        return False

    ver = preflight.ollama_version(url)
    if not preflight.ollama_floor_ok(ver):
        out(f"⚠ Ollama verzija {ver or 'nepoznata'} < 0.5.0 — preporucen upgrade "
            "(winget upgrade Ollama.Ollama). Nastavljam.")

    fits = preflight.model_fits(cfg)
    rec = preflight.recommend_chat_model(fits)
    out("Dostupni modeli (za ovaj hardver):")
    names = render_model_catalog(fits, rec, out=out)
    if not names:
        out("Nijedan model ne stane u RAM ovog racunala — postavi kasnije (Postavke).")
        return True
    choices = names + ["Preskoci — postavi kasnije"]
    default_idx = names.index(rec) if rec in names else 0
    idx = tui.prompt_choice("Odaberi JEDAN model:", choices, default=default_idx,
                            input_fn=input_fn, out=out)
    if idx == len(names):   # skip opcija
        return True
    model = names[idx]

    out(f"Skidam {model} (prekid je siguran — nastavlja gdje je stalo)...")
    if not preflight.ollama_pull(model, url, out=out):
        out("Model nije skinut. Pokreni setup ponovno ili postavi kasnije u Postavkama.")
        return tui.prompt_yes_no("Nastavi setup bez modela?", default=True,
                                 input_fn=input_fn, out=out)

    emb = setup_embedding(spine, cfg, out=out)
    from ragspine.business import model_settings
    model_settings.save(spine, "ollama", model=model, ollama_url=url,
                        embed_model=emb or "", user="setup")
    if not self_test(spine, cfg, input_fn=input_fn, out=out):
        out("⚠ Self-test nije prosao — model je spremljen, provjeri ga kasnije u Postavkama.")
    return True
```

Redoslijed `save` NAKON embedding-koraka: jedan poziv `save` sa svime (provider, model, ollama_url, embed_model) — bez dvostrukog pisanja.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py tests/test_model_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): stranica 3 (model) — katalog s fit-pillovima, jedan izbor, pull, spremanje"
```

---

### Task 7: Wiring — stage 3 u run(), mark_complete pomak, poruke

**Files:**
- Modify: `ragspine/ops/wizard.py:67-98` (`run`)
- Test: `tests/test_wizard.py` (ažuriraj postojeće + dodaj)

**Interfaces:**
- Consumes: `page_model` (Task 6), `wizard_state.set_stage/mark_complete`.
- Produces: `run` sa stage<3 blokom; `mark_complete` sad dolazi iza stagea 3 (komentar iz P1 to već najavljuje); završna poruka ažurirana.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard.py  (dodaj; postojeci test_run_success_marks_setup_complete
# vjerojatno treba mock page_model — azuriraj ga da monkeypatcha
# wizard.page_model = lambda *a, **k: True i dalje prolazi)
def test_run_reaches_stage3_and_completes(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    from ragspine.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "page_preduvjeti", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_operater", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(wizard, "page_model", lambda *a, **k: called.append(1) or True)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert called == [1]
    assert ws.get_stage(s) == 3
    assert ws.is_complete(s) is True


def test_run_no_complete_when_model_page_cancelled(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    from ragspine.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard, "page_preduvjeti", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_operater", lambda *a, **k: True)
    monkeypatch.setattr(wizard, "page_model", lambda *a, **k: False)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert ws.get_stage(s) == 2          # stranice 1-2 prosle
    assert ws.is_complete(s) is False    # model otkazan -> nije complete


def test_run_resume_from_stage2_runs_only_model_page(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    from ragspine.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    ws.set_stage(s, 2)
    ran = []
    monkeypatch.setattr(wizard, "page_preduvjeti", lambda *a, **k: ran.append("p1") or True)
    monkeypatch.setattr(wizard, "page_operater", lambda *a, **k: ran.append("p2") or True)
    monkeypatch.setattr(wizard, "page_model", lambda *a, **k: ran.append("p3") or True)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert ran == ["p3"]
    assert ws.is_complete(s) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -k "stage3 or model_page_cancelled or resume_from_stage2" -v`
Expected: FAIL — `run` nema stage-3 blok (stage ostaje 2, complete se postavlja i bez modela)

- [ ] **Step 3: Write minimal implementation**

U `run()` (wizard.py), unutar postojećeg `try`, iza stage<2 bloka dodaj:

```python
        if stage < 3:
            if not page_model(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na modelu. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 3)
```

I zamijeni završni blok (mark_complete + poruke):

```python
    # P2 pokriva stranice 1-3; mark_complete se pomice dalje kako stranice
    # 4-6 stizu u P3-P4 (poziv ide iza ZADNJE implementirane stranice).
    wizard_state.mark_complete(spine)
    out("P2 gotov: preduvjeti + operater + model. Setup je dovrsen — web sucelje je dostupno.")
    out("Stranice 4-6 (mreza/HTTPS/servis, mape, sazetak) slijede u P3-P4.")
```

Ažuriraj i postojeće testove koji zovu `run` (npr. `test_run_success_marks_setup_complete`, `test_run_handles_eof_without_traceback`): dodaj im `monkeypatch.setattr(wizard, "page_model", lambda *a, **k: True)` (ili mock preflight/tui po uzoru na postojeće) tako da ne diraju mrežu i da očekivani stage odgovara novom toku.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py tests/test_wizard_state.py tests/test_firstrun.py tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Puni suite + cyrillic gate**

Run: `python -m pytest -q`
Expected: sve zeleno (bez novih padova; `tests/test_no_cyrillic.py` zelen)

- [ ] **Step 6: Commit**

```bash
git add ragspine/ops/wizard.py tests/test_wizard.py
git commit -m "feat(setup): stranica 3 u run() — stage 3, mark_complete iza modela"
```

---

## Self-Review (autor)

**Spec coverage (P2 dio speca — str.3):**
- Ollama health prije svega + floor ≥0.5.0 + auto-start + „preskoči" grana → Task 1, 6. ✓
- Bogat katalog + „za što je dobar" + fit-pill 🟢🟡🔴 + GPU + PREPORUKA → Task 2, 6. ✓
- JEDAN model → download resumable s progresom → Task 3, 6 (resume radi Ollama daemon — dovoljno ponovno pozvati). ✓
- Embedding auto s fit-pillom + download+verify + fallback → Task 4 (bge-m3 preko fastembed `BAAI/bge-m3`; ako fastembed build to ime ne podržava, `download_model` vrati error i fallback preskače na default — degradacija je dizajnirana, implementer nek zabilježi ishod). ✓
- Self-test: ne-prazan bounded odgovor, timeout cold-load (LLMClient 120 s + poruka „zna trajati i minutu"), 3 retry, Preskoči/Cancel, soft regex, kvar ne poništava setup → Task 5, 6. ✓
- Spremanje odabira → postojeći `model_settings.save` (config_overrides modul 'model') → Task 6. ✓
- `setup_complete` iza zadnje implementirane stranice → Task 7. ✓
- Svjesno ODGOĐENO: winget auto-install + proxy (P3, uz Windows-elevation posao); VLM OCR (Postavke, spec §6); stranice 4-6 (P3-P4).

**Placeholder scan:** čisto — jedini namjerni marker je pokvareni assert u Task 6 Step 1, eksplicitno označen za uklanjanje.

**Type consistency:** `preflight.ollama_version/ollama_floor_ok/start_ollama/ollama_pull/recommend_chat_model/model_fits(desc)/fit_pill`, `wizard.choose_embed_model/setup_embedding/_download_embed/_llm_complete/self_test/render_model_catalog/page_model`, `model_settings.save(spine, provider, model=, ollama_url=, embed_model=, user=)` — imena i potpisi usklađeni kroz taskove; `page_model` konzumira točno ono što Taskovi 1-5 proizvode.

## Runnable check

`pytest tests/test_wizard.py tests/test_preflight.py tests/test_model_settings.py tests/test_no_cyrillic.py -q` zelen + `python -m pytest -q` zelen.
