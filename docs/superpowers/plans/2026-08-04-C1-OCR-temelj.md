# C1 — OCR temelj — Implementacijski plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skenirani PDF-ovi u spojenim mapama postaju pretraživi lokalnim tesseractom (hrv+eng), s VLM fallbackom, upisom u isti PDF, dostupno kroz „OCR-aj mapu".

**Architecture:** Nadograđuje `ragspine/docs/ocr.py` (VLM-only) na dva motora: novi tesseract put + dispatcher `ocr_page_best`; `ocr_pdf` piše in-place (inkrementalni save); scoping proširen na `mount_roots`; API `POST /folders/{id}/ocr` + audit + dashboard dugme.

**Tech Stack:** Python 3.11+, tesseract (binarka, hrv+eng), PyMuPDF (fitz), PIL (test-fixture render), FastAPI, `core.subproc.run_isolated`.

## Global Constraints
- OCR piše U ISTI PDF (inkrementalni save; NAS backup pokriva). Vizual netaknut.
- Svi OCR putovi scoped: realpath pod `mount_roots ∪ nas_root ∪ data_dir`, anti-symlink.
- tesseract/VLM greška → `""` (degradira, ne ruši); prazan OCR se ne indeksira.
- Idempotentno: `ocr_pdf` preskače PDF s tekstualnim slojem osim `force=True`.
- tesseract dobiva PNG preko privremene datoteke (`run_isolated` nema stdin).
- Data-driven UI: `textContent`, `require_user_web`.

---

### Task 1: Config `ocr_langs`

**Files:**
- Modify: `ragspine/config.py` (dataclass polje + `from_env`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `cfg.ocr_langs: str` (default `"hrv+eng"`).

- [ ] **Step 1: Failing test**

```python
# tests/test_config.py
def test_ocr_langs_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    from ragspine.config import Config
    assert Config.from_env().ocr_langs == "hrv+eng"
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: ocr_langs`)

Run: `python -m pytest tests/test_config.py::test_ocr_langs_default -q`

- [ ] **Step 3: Implement** — dodaj `ocr_langs: str` u `Config` dataclass i u `from_env`:
`ocr_langs=e("RAGSPINE_OCR_LANGS", "hrv+eng"),`

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/config.py tests/test_config.py
git commit -m "feat(config): ocr_langs (default hrv+eng)"
```

---

### Task 2: Tesseract motor

**Files:**
- Modify: `ragspine/docs/ocr.py` (`ocr_page_tesseract`, `tesseract_available`)
- Test: `tests/test_ocr_tesseract.py`

**Interfaces:**
- Consumes: `core.subproc.run_isolated(cmd, timeout) -> (rc, out, err)`, `cfg.ocr_langs`.
- Produces:
  - `ocr.tesseract_available() -> bool` (`shutil.which("tesseract")`).
  - `ocr.ocr_page_tesseract(png: bytes, cfg) -> str` — PNG → tekst; `""` na grešci/nedostupno.

- [ ] **Step 1: Failing test**

```python
# tests/test_ocr_tesseract.py
import pytest
from ragspine.docs import ocr

def _png_with_text(text="PDV 25%"):
    Image = pytest.importorskip("PIL.Image"); ImageDraw = pytest.importorskip("PIL.ImageDraw")
    img = Image.new("RGB", (320, 90), "white")
    ImageDraw.Draw(img).text((12, 30), text, fill="black")
    import io; buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

def test_tesseract_reads_text(cfg):
    if not ocr.tesseract_available():
        pytest.skip("tesseract nije na PATH-u")
    out = ocr.ocr_page_tesseract(_png_with_text("PDV 25"), cfg)
    assert "PDV" in out or "25" in out

def test_tesseract_missing_binary_returns_empty(cfg, monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    assert ocr.ocr_page_tesseract(b"notapng", cfg) == ""
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement** — u `ocr.py`:

```python
import shutil, tempfile

def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None

def ocr_page_tesseract(png: bytes, cfg) -> str:
    if not tesseract_available():
        return ""
    langs = getattr(cfg, "ocr_langs", "hrv+eng") or "hrv+eng"
    from ragspine.core.subproc import run_isolated
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png); tmp = f.name
        rc, out, _err = run_isolated(["tesseract", tmp, "stdout", "-l", langs], timeout=120)
        return out if rc == 0 else ""
    except Exception:
        return ""
    finally:
        if tmp:
            try: os.remove(tmp)
            except OSError: pass
```

- [ ] **Step 4: Run — PASS** (`python -m pytest tests/test_ocr_tesseract.py -q`)

- [ ] **Step 5: Commit**

```bash
git add ragspine/docs/ocr.py tests/test_ocr_tesseract.py
git commit -m "feat(ocr): tesseract lokalni motor (hrv+eng)"
```

---

### Task 3: Dispatcher `ocr_page_best`

**Files:**
- Modify: `ragspine/docs/ocr.py`
- Test: `tests/test_ocr_dispatch.py`

**Interfaces:**
- Consumes: `ocr_page_tesseract`, `ocr_page` (VLM), `cfg.ocr_url`.
- Produces: `ocr.ocr_page_best(png, cfg, transport=None) -> tuple[str, str]` — `(tekst, motor)`,
  motor ∈ `"tesseract"|"vlm"|"none"`.

- [ ] **Step 1: Failing test**

```python
# tests/test_ocr_dispatch.py
from ragspine.docs import ocr

def test_uses_tesseract_when_enough(cfg, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_page_tesseract", lambda png, c: "dovoljno teksta ovdje za prag")
    called = []
    monkeypatch.setattr(ocr, "ocr_page", lambda *a, **k: called.append(1) or "VLM")
    text, engine = ocr.ocr_page_best(b"x", cfg)
    assert engine == "tesseract" and not called   # VLM se NE zove

def test_falls_back_to_vlm_when_tesseract_empty(cfg, monkeypatch):
    cfg.ocr_url = "https://vlm.example"
    monkeypatch.setattr(ocr, "ocr_page_tesseract", lambda png, c: "")
    monkeypatch.setattr(ocr, "ocr_page", lambda png, c, transport=None: "tekst s vlm-a")
    text, engine = ocr.ocr_page_best(b"x", cfg)
    assert engine == "vlm" and text == "tekst s vlm-a"

def test_no_engine_returns_none(cfg, monkeypatch):
    cfg.ocr_url = ""
    monkeypatch.setattr(ocr, "ocr_page_tesseract", lambda png, c: "")
    text, engine = ocr.ocr_page_best(b"x", cfg)
    assert engine == "none" and text == ""
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

```python
_MIN_OK_CHARS = 20

def ocr_page_best(png: bytes, cfg, transport=None):
    t = ocr_page_tesseract(png, cfg)
    if len(t.strip()) >= _MIN_OK_CHARS:
        return t, "tesseract"
    if getattr(cfg, "ocr_url", ""):
        v = ocr_page(png, cfg, transport=transport)
        if len(v.strip()) > len(t.strip()):
            return v, "vlm"
    if t.strip():
        return t, "tesseract"
    return "", "none"
```

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/docs/ocr.py tests/test_ocr_dispatch.py
git commit -m "feat(ocr): dispatcher ocr_page_best (tesseract → VLM fallback)"
```

---

### Task 4: Scoping na mount_roots + in-place upis

**Files:**
- Modify: `ragspine/docs/ocr.py` (`resolve_scoped_path`, `ocr_pdf`)
- Test: `tests/test_ocr_scope.py`, `tests/test_ocr_inplace.py`

**Interfaces:**
- Consumes: `cfg.mount_roots`, `write_text_layer`.
- Produces: `ocr_pdf(..., force)` piše u isti `path` (in-place), vraća `{..., out==path, engines}`.

- [ ] **Step 1: Failing test (scope)**

```python
# tests/test_ocr_scope.py
import os, pytest
from ragspine.docs import ocr
from ragspine.config import Config

def _cfg(tmp_path, roots):
    old = dict(os.environ)
    os.environ.update({"RAGSPINE_DATA_DIR": str(tmp_path / "d"),
                       "RAGSPINE_MOUNT_ROOTS": ",".join(roots)})
    try: return Config.from_env()
    finally: os.environ.clear(); os.environ.update(old)

def test_scope_allows_mount_root(tmp_path):
    share = tmp_path / "share"; (share / "a").mkdir(parents=True)
    f = share / "a" / "x.pdf"; f.write_bytes(b"%PDF")
    cfg = _cfg(tmp_path, [str(share)])
    assert ocr.resolve_scoped_path(cfg, str(f)) == os.path.realpath(str(f))

def test_scope_rejects_outside(tmp_path):
    share = tmp_path / "share"; share.mkdir()
    outside = tmp_path / "other.pdf"; outside.write_bytes(b"%PDF")
    cfg = _cfg(tmp_path, [str(share)])
    with pytest.raises(ValueError):
        ocr.resolve_scoped_path(cfg, str(outside))
```

- [ ] **Step 2: Run — FAIL** (outside trenutno prolazi jer scope gleda samo nas_root/data_dir)

- [ ] **Step 3: Implement** — `resolve_scoped_path`:

```python
def resolve_scoped_path(cfg, path: str) -> str:
    roots = [os.path.realpath(r) for r in (cfg.mount_roots or [])]
    roots += [os.path.realpath(cfg.nas_root or cfg.data_dir)]
    resolved = os.path.realpath(path)
    for root in roots:
        if root and os.path.commonpath([resolved, root]) == root:
            return resolved
    raise ValueError(f"put izvan dozvoljenih korijena: {path!r}")
```

`ocr_pdf` — in-place upis + engines:

```python
def ocr_pdf(spine, cfg, path: str, transport=None, force: bool = False) -> dict:
    path = resolve_scoped_path(cfg, path)
    if not force and has_text_layer(path):
        return {"skipped": True, "out": path, "pages": 0, "engines": {}}
    pairs = [ocr_page_best(png, cfg, transport=transport) for png in rasterize(path)]
    page_texts = [t for t, _e in pairs]
    engines = {}
    for _t, e in pairs:
        engines[e] = engines.get(e, 0) + 1
    write_text_layer(path, page_texts, out_path=path)   # in-place
    full_text = "\n\n".join(t for t in page_texts if t)
    if not full_text.strip():
        return {"skipped": False, "pages": len(page_texts), "out": path, "ocr_empty": True, "engines": engines}
    ingest_text(spine, full_text, title=os.path.basename(path), path=path)
    return {"skipped": False, "pages": len(page_texts), "out": path, "engines": engines}
```

- [ ] **Step 4: Test (in-place)**

```python
# tests/test_ocr_inplace.py
import os, pytest
from ragspine.docs import ocr
from ragspine.config import Config

def _cfg(tmp_path, share):
    old = dict(os.environ)
    os.environ.update({"RAGSPINE_DATA_DIR": str(tmp_path / "d"),
                       "RAGSPINE_MOUNT_ROOTS": str(share)})
    try: return Config.from_env()
    finally: os.environ.clear(); os.environ.update(old)

def test_ocr_pdf_writes_text_layer_in_place(spine, tmp_path, monkeypatch):
    pytest.importorskip("fitz")
    share = tmp_path / "share"; share.mkdir()
    import fitz
    p = str(share / "skan.pdf")
    doc = fitz.open(); doc.new_page(); doc.save(p); doc.close()   # PDF bez teksta
    cfg = _cfg(tmp_path, share)
    monkeypatch.setattr(ocr, "ocr_page_best", lambda png, c, transport=None: ("Ovo je OCR tekst za test.", "tesseract"))
    res = ocr.ocr_pdf(spine, cfg, p)
    assert res["out"] == os.path.realpath(p) and not res.get("skipped")
    assert ocr.has_text_layer(p)   # isti PDF sad ima tekst
```

Run: `python -m pytest tests/test_ocr_scope.py tests/test_ocr_inplace.py -q` → PASS

Napomena: ako `doc.save(path, incremental=True)` zatreba u praksi (fitz odbija save preko
otvorenog originala) — `write_text_layer` neka save-a u `path + ".tmp"` pa `os.replace`.
Ovaj plan koristi `out_path=path`; ako test padne na fitz „save to original", u
`write_text_layer` dodaj: `tmp=out+".tmp"; doc.save(tmp); doc.close(); os.replace(tmp,out)`.

- [ ] **Step 5: Commit**

```bash
git add ragspine/docs/ocr.py tests/test_ocr_scope.py tests/test_ocr_inplace.py
git commit -m "feat(ocr): scope na mount_roots + in-place upis pretraživog sloja"
```

---

### Task 5: `audit_folder` + `bulk_ocr` na dva motora

**Files:**
- Modify: `ragspine/docs/ocr.py` (`audit_folder`, `bulk_ocr`)
- Test: `tests/test_ocr_bulk.py`

**Interfaces:**
- Produces:
  - `ocr.audit_folder(cfg, base) -> {n_pdf, n_pdf_no_text, sample}`.
  - `ocr.bulk_ocr(spine, cfg, folder, transport=None) -> {processed, skipped, engines, errors}`.

- [ ] **Step 1: Failing test**

```python
# tests/test_ocr_bulk.py
import os, pytest
from ragspine.docs import ocr
from ragspine.config import Config

def _cfg(tmp_path, share):
    old = dict(os.environ)
    os.environ.update({"RAGSPINE_DATA_DIR": str(tmp_path/"d"), "RAGSPINE_MOUNT_ROOTS": str(share)})
    try: return Config.from_env()
    finally: os.environ.clear(); os.environ.update(old)

def test_audit_counts_no_text(spine, tmp_path):
    fitz = pytest.importorskip("fitz")
    share = tmp_path / "share"; share.mkdir()
    d = fitz.open(); d.new_page(); d.save(str(share/"skan.pdf")); d.close()      # bez teksta
    d = fitz.open(); p = d.new_page(); p.insert_text((72,72), "ima teksta ovdje puno"); d.save(str(share/"txt.pdf")); d.close()
    cfg = _cfg(tmp_path, share)
    a = ocr.audit_folder(cfg, str(share))
    assert a["n_pdf"] == 2 and a["n_pdf_no_text"] == 1

def test_bulk_ocr_processes(spine, tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    share = tmp_path / "share"; share.mkdir()
    d = fitz.open(); d.new_page(); d.save(str(share/"skan.pdf")); d.close()
    cfg = _cfg(tmp_path, share)
    monkeypatch.setattr(ocr, "ocr_page_best", lambda png, c, transport=None: ("tekst dovoljne duljine za test.", "tesseract"))
    res = ocr.bulk_ocr(spine, cfg, str(share))
    assert res["processed"] == 1 and res["engines"].get("tesseract", 0) >= 1
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

```python
def audit_folder(cfg, base: str) -> dict:
    base = resolve_scoped_path(cfg, base)
    n_pdf = n_no = 0; sample = []
    for root, _d, files in os.walk(base):
        for f in files:
            if not f.lower().endswith(".pdf"):
                continue
            n_pdf += 1
            fp = os.path.join(root, f)
            try:
                if not has_text_layer(fp):
                    n_no += 1
                    if len(sample) < 20:
                        sample.append(fp)
            except Exception:
                pass
    return {"n_pdf": n_pdf, "n_pdf_no_text": n_no, "sample": sample}
```

`bulk_ocr` — zamijeni `ocr_page` upotrebu; agregiraj `engines`:

```python
def bulk_ocr(spine, cfg, folder: str, transport=None) -> dict:
    result = {"processed": 0, "skipped": 0, "engines": {}, "errors": []}
    for root, _d, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            fpath = os.path.join(root, fname)
            try:
                res = ocr_pdf(spine, cfg, fpath, transport=transport)
            except Exception as e:
                result["errors"].append(f"{fpath}: {e}")
                continue
            result["skipped" if res["skipped"] else "processed"] += 1
            for eng, n in (res.get("engines") or {}).items():
                result["engines"][eng] = result["engines"].get(eng, 0) + n
    return result
```

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/docs/ocr.py tests/test_ocr_bulk.py
git commit -m "feat(ocr): audit_folder + bulk_ocr na dva motora"
```

---

### Task 6: Endpointi `POST /folders/{id}/ocr` + `GET /folders/{id}/ocr/audit`

**Files:**
- Modify: `ragspine/web/api.py`
- Test: `tests/test_folder_ocr_api.py`

**Interfaces:**
- Consumes: `folders._scoped`, `ocr.audit_folder/bulk_ocr`, `notifications`.
- Produces: `POST /folders/{id}/ocr` → `{processed, skipped, engines, notified}`;
  `GET /folders/{id}/ocr/audit` → `{n_pdf, n_pdf_no_text}`.

- [ ] **Step 1: Failing test**

```python
# tests/test_folder_ocr_api.py
import os, pytest
from fastapi.testclient import TestClient
from ragspine.business import folders
from ragspine.config import Config
from ragspine.web.api import create_app
from ragspine.web.deps import add_user
from ragspine.docs import ocr

def _cfg(tmp_path, share):
    old = dict(os.environ)
    os.environ.update({"RAGSPINE_DATA_DIR": str(tmp_path/"d"), "RAGSPINE_MOUNT_ROOTS": str(share)})
    try: return Config.from_env()
    finally: os.environ.clear(); os.environ.update(old)

def _tok(c, spine):
    add_user(spine, "ana", "pw")
    return c.post("/auth/login", json={"username":"ana","password":"pw"}).json()["token"]

def test_folder_ocr_endpoint(spine, tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    share = tmp_path / "KLIJENTI"; share.mkdir()
    d = fitz.open(); d.new_page(); d.save(str(share/"skan.pdf")); d.close()
    cfg = _cfg(tmp_path, share)
    fid = folders.register(spine, cfg, str(share), "klijenti")["id"]
    monkeypatch.setattr(ocr, "ocr_page_best", lambda png, c, transport=None: ("tekst dovoljne duljine.", "tesseract"))
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine); h = {"Authorization": f"Bearer {tok}"}
    r = c.post(f"/folders/{fid}/ocr", headers=h)
    assert r.status_code == 200 and r.json()["processed"] == 1
    notifs = c.get("/notifications.json", headers=h).json()
    assert any(n["kind"] == "folder_ocred" for n in notifs)
    a = c.get(f"/folders/{fid}/ocr/audit", headers=h).json()
    assert "n_pdf_no_text" in a
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement** — uz ostale folder rute:

```python
    @app.post("/folders/{folder_id}/ocr")
    def folder_ocr(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.docs import ocr as ocr_mod
        row = spine.read().execute("SELECT path, label FROM folders WHERE id=?", (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznata mapa")
        base = folders_mod._scoped(cfg, row["path"])
        res = ocr_mod.bulk_ocr(spine, cfg, base)
        name = row["label"] or row["path"]
        body = f"OCR gotov za „{name}\": {res['processed']} obrađeno, {res['skipped']} preskočeno."
        with spine.write() as conn:
            conn.execute("INSERT INTO notifications(kind, body) VALUES('folder_ocred', ?)", (body,))
        return {**res, "notified": True}

    @app.get("/folders/{folder_id}/ocr/audit")
    def folder_ocr_audit(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.docs import ocr as ocr_mod
        row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznata mapa")
        base = folders_mod._scoped(cfg, row["path"])
        return ocr_mod.audit_folder(cfg, base)
```

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/web/api.py tests/test_folder_ocr_api.py
git commit -m "feat(api): OCR-aj mapu endpoint + audit + folder_ocred obavijest"
```

---

### Task 7: Dashboard dugme „OCR-aj mapu"

**Files:**
- Modify: `ragspine/web/templates_ui.py` (`renderOrientation`)
- Test: `tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: `orientation.folders[].scan.n_pdf_no_text`, `POST /folders/{id}/ocr`.

- [ ] **Step 1: Failing test** — HTML sadrži rukovatelja OCR-a u dashboard JS-u:

```python
# tests/test_dashboard_ui.py
def test_dashboard_has_ocr_action_js():
    from ragspine.web.templates_ui import dashboard_page
    html = dashboard_page()
    assert "/folders/" in html and "/ocr" in html and "OCR-aj mapu" in html
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement** — u `renderOrientation`, nakon `scanBtn`:

```javascript
    if ((f.scan || {}).n_pdf_no_text > 0) {
      var ocrBtn = document.createElement('button'); ocrBtn.className = 'btn';
      ocrBtn.textContent = 'OCR-aj mapu (' + f.scan.n_pdf_no_text + ')';
      ocrBtn.addEventListener('click', function () {
        ocrBtn.disabled = true; ocrBtn.textContent = 'OCR u tijeku…';
        fetch('/folders/' + f.id + '/ocr', {method:'POST', credentials:'same-origin'})
          .then(function(){ loadDashboard(); });
      });
      row.appendChild(ocrBtn);
    }
```

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/web/templates_ui.py tests/test_dashboard_ui.py
git commit -m "feat(dashboard): OCR-aj mapu dugme (kad ima PDF-ova bez teksta)"
```

---

### Task 8: Puna suita

- [ ] **Step 1:** `python -m pytest -q` → sve zeleno.
- [ ] **Step 2: Commit** (ako je bilo popravaka) + push na CI.

---

## Self-Review (autor plana)
- **Spec coverage:** tesseract motor → T2; dispatcher → T3; scope mount_roots + in-place → T4;
  audit + bulk dva motora → T5; endpointi → T6; dashboard dugme → T7; config → T1. Sve pokriveno.
- **Placeholderi:** nema; svaki task pravi test+kod.
- **Tipovi:** `ocr_page_best`→`(str,str)` konzistentan T3↔T4↔T5; `ocr_pdf` vraća `engines` T4↔T5;
  `audit_folder`→`n_pdf/n_pdf_no_text/sample` T5↔T6.
- **Rizik zabilježen:** fitz in-place save (`out_path=path`) — fallback temp+os.replace opisan u T4.
