# A+B — Spajanje + otkrivanje klijenata — Implementacijski plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kad vlasnik spoji shareanu mapu, RAGSPINE ju read-only popiše, javi obavijest s predloženim akcijama, zapamti napomene i otkrije klijente iz naziva podmapa — sve dostupno kroz lijevi sidebar + dashboard.

**Architecture:** Novi read-only skener stabla (`folder_scan.py`) puni `folder_scan` tablicu; API pokreće scan + kreira `notifications`; `client_discovery.py` iz naziva podmapa predlaže klijente koje vlasnik pregleda pa potvrdi (idempotentan upsert `clients.nas_folder`). UI shell prelazi na lijevi sidebar; dashboard dobiva orijentacijsku karticu. Ništa se na disku ne mijenja.

**Tech Stack:** Python 3.11+, FastAPI, SQLite (raw param SQL), fitz/pymupdf (optional, za detekciju PDF-bez-teksta), vanilla JS (textContent, script_json).

## Global Constraints
- Jedan ured, jedan install; sve read-only nad diskom u ovom komadu (nula move/delete).
- Scoped realpath + simlink-escape kroz `folders._scoped` — vrijedi za svaki scan.
- Idempotentno: ponovni scan/discover ne duplicira klijente ni obavijesti.
- Data-driven UI: `textContent` za API podatke, `script_json` za inline `<script>`, `require_user_web` auth.
- Period regex, SQL parametrizirano; nema stringanja korisničkog inputa u SQL.
- fitz je optional (`optional.need`) — bez njega PDF-bez-teksta = `None` (nepoznato), ne greška.

---

### Task 1: Lijevi sidebar shell + dashboard ostaje home

**Files:**
- Modify: `ragspine/web/templates_ui.py` (`page_shell`, `CSS_TOKENS`)
- Test: `tests/test_dashboard_ui.py` (dodati), `tests/test_client_ui.py` (smoke i dalje prolazi)

**Interfaces:**
- Produces: `page_shell(title, body_html, active="")` — nepromijenjen potpis, sada renderira `<aside class="sidebar">` + `<main>`.

- [ ] **Step 1: Failing test — sidebar markup**

```python
# tests/test_dashboard_ui.py
from ragspine.web.templates_ui import page_shell

def test_shell_uses_left_sidebar():
    html = page_shell("Test", "<p>x</p>", active="home")
    assert 'class="sidebar"' in html
    assert '<main' in html
    assert 'RAGSPINE' in html  # brand
    assert 'aria-current' in html or 'class="active"' in html  # aktivni link označen
```

- [ ] **Step 2: Run — expect FAIL** (`class="sidebar"` ne postoji)

Run: `python -m pytest tests/test_dashboard_ui.py::test_shell_uses_left_sidebar -q`

- [ ] **Step 3: Implement** — u `page_shell` zamijeni `<nav class="nav">…</nav>` blok sidebarom:

```python
    return f"""<!DOCTYPE html>
<html lang="hr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_e} — RAGSPINE</title><style>{CSS_TOKENS}</style></head>
<body>
<script>{_THEME_INIT_JS}</script>
<div class="layout">
<aside class="sidebar" aria-label="Glavna navigacija">
<span class="brand">RAGSPINE</span>
<nav>{nav_links}</nav>
<span class="spacer"></span>
<button type="button" id="theme-toggle" class="theme-toggle" aria-label="Promijeni temu" onclick="toggleTheme()">&#9790;</button>
<a href="/logout" class="logout">Odjava</a>
</aside>
<main class="container">
{body_html}
</main>
</div>
<script>{_THEME_TOGGLE_JS}</script><script>{_NAV_BADGE_JS}</script>
</body></html>"""
```

U `CSS_TOKENS` dodaj layout (grid: sidebar fiksne širine lijevo, main desno; na `max-width:640px` sidebar horizontalno skupljen):

```css
.layout{display:grid;grid-template-columns:220px 1fr;min-height:100vh}
.sidebar{display:flex;flex-direction:column;gap:.35rem;padding:1rem .75rem;border-right:1px solid var(--line);position:sticky;top:0;height:100vh}
.sidebar nav{display:flex;flex-direction:column;gap:.15rem}
.sidebar a{padding:.45rem .6rem;border-radius:.4rem;text-decoration:none}
.sidebar a.active{background:var(--accent-soft,rgba(0,0,0,.06));font-weight:600}
.sidebar .spacer{flex:1}
@media(max-width:640px){.layout{grid-template-columns:1fr}.sidebar{position:static;height:auto;flex-direction:row;flex-wrap:wrap;border-right:0;border-bottom:1px solid var(--line)}}
```

Aktivni link: u petlji dodaj `aria-current="page"` uz `class="active"`.

- [ ] **Step 4: Run** — `python -m pytest tests/test_dashboard_ui.py tests/test_client_ui.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/web/templates_ui.py tests/test_dashboard_ui.py
git commit -m "feat(ui): lijevi sidebar shell (dashboard ostaje home)"
```

---

### Task 2: SKENER uloga mape

**Files:**
- Modify: `ragspine/business/folders.py` (`ROLES`)
- Test: `tests/test_folders.py`

**Interfaces:**
- Produces: `folders.ROLES == ("propisi", "klijenti", "ostalo", "skener")`

- [ ] **Step 1: Failing test**

```python
# tests/test_folders.py
from ragspine.business import folders

def test_skener_is_valid_role():
    assert "skener" in folders.ROLES
```

- [ ] **Step 2: Run — FAIL**

Run: `python -m pytest tests/test_folders.py::test_skener_is_valid_role -q`

- [ ] **Step 3: Implement** — `ROLES = ("propisi", "klijenti", "ostalo", "skener")` u `folders.py`.

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/business/folders.py tests/test_folders.py
git commit -m "feat(folders): SKENER uloga mape (tok u komadu E)"
```

---

### Task 3: Read-only skener stabla + folder_scan tablica

**Files:**
- Create: `ragspine/business/folder_scan.py`
- Modify: `ragspine/core/spine.py` (SCHEMA: tablica `folder_scan`)
- Test: `tests/test_folder_scan.py`

**Interfaces:**
- Consumes: `folders._scoped(cfg, path)` (scoped realpath), `optional.need("fitz", …)`.
- Produces:
  - `folder_scan.scan(spine, cfg, folder_id) -> dict` — `{n_subdirs, n_docs, n_pdf, n_pdf_no_text, at}`; upisuje u `folder_scan`; read-only nad diskom.
  - `folder_scan.latest(spine, folder_id) -> dict | None`.
  - `folder_scan.pdf_has_text(path) -> bool | None` (None ako fitz nedostupan).

- [ ] **Step 1: Failing test**

```python
# tests/test_folder_scan.py
import os
from ragspine.business import folders, folder_scan

def _mk_klijenti(tmp_path):
    root = tmp_path / "share"; kl = root / "KLIJENTI"
    (kl / "PERIĆ PERO" / "2024").mkdir(parents=True)
    (kl / "PERIĆ PERO" / "2024" / "doh.txt").write_text("x", encoding="utf-8")
    (kl / "PODUZEĆE X D.O.O.").mkdir(parents=True)
    (kl / "PODUZEĆE X D.O.O." / "ugovor.pdf").write_bytes(b"%PDF-1.4 nije pravi")
    return root, kl

def test_scan_counts(spine, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "nas_root", str(tmp_path / "share"), raising=False)
    root, kl = _mk_klijenti(tmp_path)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    res = folder_scan.scan(spine, cfg, fid)
    assert res["n_subdirs"] == 2          # dvije klijentske mape (prva razina)
    assert res["n_docs"] >= 2             # doh.txt + ugovor.pdf
    assert res["n_pdf"] == 1
    assert folder_scan.latest(spine, fid)["n_subdirs"] == 2
```

- [ ] **Step 2: Run — FAIL** (`No module named folder_scan`)

- [ ] **Step 3: Implement**

`spine.py` SCHEMA (uz ostale CREATE TABLE):

```sql
CREATE TABLE IF NOT EXISTS folder_scan(folder_id INTEGER PRIMARY KEY, at TEXT DEFAULT (datetime('now')),
  n_subdirs INTEGER, n_docs INTEGER, n_pdf INTEGER, n_pdf_no_text INTEGER, summary_json TEXT);
```

`ragspine/business/folder_scan.py`:

```python
"""Read-only popis stabla spojene mape: broji podmape/dokumente/PDF-ove i PDF-ove
bez pretraživog teksta. Ništa se ne mijenja na disku. Puni OCR je komad C."""
import json, os
from ragspine.business import folders
from ragspine.core import optional

_DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md", ".odt", ".rtf", ".png", ".jpg", ".jpeg"}


def pdf_has_text(path: str):
    """True/False ima li PDF tekstualni sloj; None ako fitz nedostupan."""
    fitz = optional.need("fitz", "PDF tekst-detekcija")
    if fitz is None:
        return None
    try:
        doc = fitz.open(path)
        try:
            return any(page.get_text().strip() for page in doc)
        finally:
            doc.close()
    except Exception:
        return False


def scan(spine, cfg, folder_id: int) -> dict:
    row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise ValueError("nepoznata mapa")
    base = folders._scoped(cfg, row["path"])  # scoped realpath, simlink-escape blokiran
    n_subdirs = sum(1 for e in os.scandir(base) if e.is_dir())
    n_docs = n_pdf = n_pdf_no_text = 0
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in _DOC_EXT:
                n_docs += 1
            if ext == ".pdf":
                n_pdf += 1
                has = pdf_has_text(os.path.join(dirpath, f))
                if has is False:
                    n_pdf_no_text += 1
    summary = {"n_subdirs": n_subdirs, "n_docs": n_docs, "n_pdf": n_pdf, "n_pdf_no_text": n_pdf_no_text}
    with spine.write() as c:
        c.execute("""INSERT INTO folder_scan(folder_id, at, n_subdirs, n_docs, n_pdf, n_pdf_no_text, summary_json)
            VALUES(?,datetime('now'),?,?,?,?,?)
            ON CONFLICT(folder_id) DO UPDATE SET at=excluded.at, n_subdirs=excluded.n_subdirs,
            n_docs=excluded.n_docs, n_pdf=excluded.n_pdf, n_pdf_no_text=excluded.n_pdf_no_text,
            summary_json=excluded.summary_json""",
            (folder_id, n_subdirs, n_docs, n_pdf, n_pdf_no_text, json.dumps(summary)))
    return {**summary, "at": "now"}


def latest(spine, folder_id: int):
    r = spine.read().execute("SELECT * FROM folder_scan WHERE folder_id=?", (folder_id,)).fetchone()
    return dict(r) if r else None
```

- [ ] **Step 4: Run — PASS** (`python -m pytest tests/test_folder_scan.py -q`)

- [ ] **Step 5: Commit**

```bash
git add ragspine/business/folder_scan.py ragspine/core/spine.py tests/test_folder_scan.py
git commit -m "feat(scan): read-only popis spojene mape + folder_scan tablica"
```

---

### Task 4: Scan endpoint + obavijest „spojeno, što dalje?"

**Files:**
- Modify: `ragspine/web/api.py` (`POST /folders/{id}/scan`, `GET /folders/{id}/scan`; obavijest u `register`/scan)
- Test: `tests/test_folder_scan_api.py`

**Interfaces:**
- Consumes: `folder_scan.scan/latest`, `spine.audit`, `notifications` tablica.
- Produces: `POST /folders/{id}/scan` → `{...counts, notified: True}`.

- [ ] **Step 1: Failing test**

```python
# tests/test_folder_scan_api.py
from fastapi.testclient import TestClient
from ragspine.web.api import create_app
from ragspine.web.deps import add_user
from ragspine.business import folders

def _tok(c, spine):
    add_user(spine, "ana", "pw")
    return c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]

def test_scan_endpoint_creates_notification(spine, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "nas_root", str(tmp_path), raising=False)
    kl = tmp_path / "KLIJENTI"; (kl / "PERIĆ PERO").mkdir(parents=True)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine)
    r = c.post(f"/folders/{fid}/scan", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["n_subdirs"] == 1
    notifs = c.get("/notifications.json", headers={"Authorization": f"Bearer {tok}"}).json()
    assert any(n["kind"] == "folder_connected" for n in notifs)
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement** — u `create_app`, uz ostale folder rute:

```python
    @app.post("/folders/{folder_id}/scan")
    def folder_scan_run(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.business import folder_scan as fs
        try:
            res = fs.scan(spine, cfg, folder_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        row = spine.read().execute("SELECT role, label, path FROM folders WHERE id=?", (folder_id,)).fetchone()
        name = row["label"] or row["path"]
        body = (f"Spojena mapa „{name}": {res['n_subdirs']} podmapa, {res['n_docs']} dokumenata, "
                f"{res['n_pdf_no_text']} PDF bez pretraživog teksta. Što želiš dalje?")
        with spine.write() as conn:
            exists = conn.execute("SELECT 1 FROM notifications WHERE kind='folder_connected' AND body=? "
                                  "AND at >= datetime('now','-1 day')", (body,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO notifications(kind, body) VALUES('folder_connected', ?)", (body,))
        return {**res, "notified": True, "role": row["role"]}

    @app.get("/folders/{folder_id}/scan")
    def folder_scan_get(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.business import folder_scan as fs
        return fs.latest(spine, folder_id) or {}
```

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/web/api.py tests/test_folder_scan_api.py
git commit -m "feat(api): scan endpoint + folder_connected obavijest"
```

---

### Task 5: Napomene → memorija

**Files:**
- Modify: `ragspine/web/api.py` (`POST /notes/folder`)
- Test: `tests/test_folder_notes.py`

**Interfaces:**
- Consumes: `memory` tablica (`spine`), postojeći `core.memory` ako ima `set`; inače raw SQL upsert.
- Produces: `POST /notes/folder` `{folder_id?, body}` → sprema `memory(user, key=note:folder:{id}|note:global, value)`.

- [ ] **Step 1: Failing test**

```python
# tests/test_folder_notes.py
from fastapi.testclient import TestClient
from ragspine.web.api import create_app
from ragspine.web.deps import add_user

def test_folder_note_persists(spine, cfg):
    add_user(spine, "ana", "pw")
    c = TestClient(create_app(spine, cfg))
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.post("/notes/folder", json={"folder_id": 3, "body": "Perić ima dva obrta"}, headers=h).status_code == 200
    row = spine.read().execute("SELECT value FROM memory WHERE key='note:folder:3'").fetchone()
    assert row["value"] == "Perić ima dva obrta"
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement** — body model + ruta:

```python
class FolderNoteBody(BaseModel):
    folder_id: int | None = None
    body: str
```

```python
    @app.post("/notes/folder")
    def folder_note(body: FolderNoteBody, user: str = Depends(require_user_web)):
        key = f"note:folder:{body.folder_id}" if body.folder_id is not None else "note:global"
        with spine.write() as c:
            c.execute("INSERT INTO memory(user,key,value) VALUES(?,?,?) "
                      "ON CONFLICT(user,key) DO UPDATE SET value=excluded.value",
                      (user, key, body.body))
        return {"ok": True, "key": key}
```

(Provjeri UNIQUE(user,key) na `memory` — postoji u SCHEMA.)

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/web/api.py tests/test_folder_notes.py
git commit -m "feat(notes): napomene po mapi/globalno u memoriju"
```

---

### Task 6: Otkrivanje klijenata iz naziva podmapa

**Files:**
- Create: `ragspine/business/client_discovery.py`
- Test: `tests/test_client_discovery.py`

**Interfaces:**
- Consumes: `folders._scoped`, `clients` tablica.
- Produces:
  - `client_discovery.discover(spine, cfg, folder_id) -> list[dict]` — `[{subdir, raw_name, guessed_type, match_id}]` (bez upisa).
  - `client_discovery.commit(spine, cfg, folder_id, items) -> dict` — `items=[{subdir, name, action, merge_id?}]`, action ∈ `import|merge|skip`; upsert `clients(name, nas_folder)`; vrati `{created, merged, skipped}`.
  - `client_discovery._guess_type(name) -> 'company'|'person'`, `_norm(name) -> str`.

- [ ] **Step 1: Failing test**

```python
# tests/test_client_discovery.py
import os
from ragspine.business import folders, client_discovery

def _mk(tmp_path):
    kl = tmp_path / "KLIJENTI"
    (kl / "PERIĆ PERO").mkdir(parents=True)
    (kl / "PODUZEĆE X D.O.O.").mkdir(parents=True)
    return kl

def test_discover_and_commit(spine, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "nas_root", str(tmp_path), raising=False)
    kl = _mk(tmp_path)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    cand = {c["raw_name"]: c for c in client_discovery.discover(spine, cfg, fid)}
    assert cand["PERIĆ PERO"]["guessed_type"] == "person"
    assert cand["PODUZEĆE X D.O.O."]["guessed_type"] == "company"
    res = client_discovery.commit(spine, cfg, fid, [
        {"subdir": "PERIĆ PERO", "name": "Perić Pero", "action": "import"},
        {"subdir": "PODUZEĆE X D.O.O.", "name": "Poduzeće X d.o.o.", "action": "skip"},
    ])
    assert res["created"] == 1 and res["skipped"] == 1
    names = [r["name"] for r in spine.read().execute("SELECT name FROM clients").fetchall()]
    assert names == ["Perić Pero"]

def test_discover_flags_existing_match(spine, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "nas_root", str(tmp_path), raising=False)
    kl = _mk(tmp_path)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name) VALUES('Perić Pero')").lastrowid
    match = {c["raw_name"]: c["match_id"] for c in client_discovery.discover(spine, cfg, fid)}
    assert match["PERIĆ PERO"] == cid

def test_commit_idempotent(spine, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "nas_root", str(tmp_path), raising=False)
    kl = _mk(tmp_path)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    item = [{"subdir": "PERIĆ PERO", "name": "Perić Pero", "action": "import"}]
    client_discovery.commit(spine, cfg, fid, item)
    client_discovery.commit(spine, cfg, fid, item)  # drugi put
    n = spine.read().execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
    assert n == 1  # bez duplikata (isti nas_folder)
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

```python
"""Otkrivanje klijenata iz naziva podmapa KLIJENTI mape (bez OIB-a). Predloži pa
potvrdi; upsert clients po nas_folder-u (idempotentno). Ne dira datoteke."""
import os, re
from ragspine.business import folders

_COMPANY = re.compile(r"\b(d\.?o\.?o\.?|j\.?d\.?o\.?o\.?|d\.?d\.?|obrt)\b", re.IGNORECASE)


def _guess_type(name: str) -> str:
    return "company" if _COMPANY.search(name or "") else "person"


def _norm(name: str) -> str:
    n = (name or "").lower()
    for a, b in zip("čćžšđ", "cczsd"):
        n = n.replace(a, b)
    words = re.findall(r"\w+", n)
    return " ".join(sorted(words))  # redoslijed riječi nebitan (prezime/ime)


def discover(spine, cfg, folder_id: int) -> list[dict]:
    row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise ValueError("nepoznata mapa")
    base = folders._scoped(cfg, row["path"])
    existing = {_norm(r["name"]): r["id"] for r in
                spine.read().execute("SELECT id, name FROM clients").fetchall()}
    out = []
    for e in sorted(os.scandir(base), key=lambda x: x.name):
        if not e.is_dir():
            continue
        out.append({"subdir": e.name, "raw_name": e.name,
                    "guessed_type": _guess_type(e.name),
                    "match_id": existing.get(_norm(e.name))})
    return out


def commit(spine, cfg, folder_id: int, items: list[dict]) -> dict:
    row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise ValueError("nepoznata mapa")
    root = os.path.realpath(cfg.nas_root)
    base = folders._scoped(cfg, row["path"])
    created = merged = skipped = 0
    with spine.write() as c:
        for it in items:
            if it.get("action") == "skip":
                skipped += 1
                continue
            subdir = it["subdir"]
            rel = os.path.relpath(os.path.join(base, subdir), root)
            if it.get("action") == "merge" and it.get("merge_id"):
                c.execute("UPDATE clients SET nas_folder=? WHERE id=?", (rel, it["merge_id"]))
                merged += 1
                continue
            # import: upsert po nas_folder (idempotentno)
            hit = c.execute("SELECT id FROM clients WHERE nas_folder=?", (rel,)).fetchone()
            if hit:
                c.execute("UPDATE clients SET name=? WHERE id=?", (it["name"], hit["id"]))
            else:
                c.execute("INSERT INTO clients(name, nas_folder) VALUES(?,?)", (it["name"], rel))
                created += 1
    return {"created": created, "merged": merged, "skipped": skipped}
```

- [ ] **Step 4: Run — PASS** (`python -m pytest tests/test_client_discovery.py -q`)

- [ ] **Step 5: Commit**

```bash
git add ragspine/business/client_discovery.py tests/test_client_discovery.py
git commit -m "feat(discovery): otkrivanje klijenata iz naziva podmapa (predloži+potvrdi)"
```

---

### Task 7: Discover endpointi + ekran uvoza + nav

**Files:**
- Modify: `ragspine/web/api.py` (`GET /clients/discover`, `POST /clients/discover/commit`, `GET /ui/klijenti-uvoz`)
- Create: `ragspine/web/templates_uvoz.py` (ekran uvoza)
- Modify: `ragspine/web/templates_ui.py` (`_NAV` +„Uvoz klijenata")
- Test: `tests/test_discover_api.py`

**Interfaces:**
- Consumes: `client_discovery.discover/commit`.
- Produces: `GET /clients/discover?folder_id=` → lista kandidata; `POST /clients/discover/commit` `{folder_id, items}` → `{created, merged, skipped}`.

- [ ] **Step 1: Failing test**

```python
# tests/test_discover_api.py
from fastapi.testclient import TestClient
from ragspine.web.api import create_app
from ragspine.web.deps import add_user
from ragspine.business import folders

def _tok(c, spine):
    add_user(spine, "ana", "pw")
    return c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]

def test_discover_and_commit_endpoints(spine, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "nas_root", str(tmp_path), raising=False)
    kl = tmp_path / "KLIJENTI"; (kl / "PERIĆ PERO").mkdir(parents=True)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    c = TestClient(create_app(spine, cfg)); tok = _tok(c, spine); h = {"Authorization": f"Bearer {tok}"}
    cand = c.get(f"/clients/discover?folder_id={fid}", headers=h).json()
    assert cand[0]["raw_name"] == "PERIĆ PERO"
    r = c.post("/clients/discover/commit", headers=h,
               json={"folder_id": fid, "items": [{"subdir": "PERIĆ PERO", "name": "Perić Pero", "action": "import"}]})
    assert r.json()["created"] == 1
    assert c.get("/ui/klijenti-uvoz", headers=h).status_code in (200, 303)  # auth-web stranica
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

Body model + rute u `api.py`:

```python
class DiscoverCommitBody(BaseModel):
    folder_id: int
    items: list[dict]
```

```python
    @app.get("/clients/discover")
    def clients_discover(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.business import client_discovery
        try:
            return client_discovery.discover(spine, cfg, folder_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/clients/discover/commit")
    def clients_discover_commit(body: DiscoverCommitBody, user: str = Depends(require_user_web)):
        from ragspine.business import client_discovery
        try:
            return client_discovery.commit(spine, cfg, body.folder_id, body.items)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/ui/klijenti-uvoz", response_class=HTMLResponse)
    def ui_klijenti_uvoz(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_uvoz import uvoz_page
        return uvoz_page()
```

`ragspine/web/templates_uvoz.py` — ekran: odabir mape (klijenti-role), gumb „Učitaj kandidate" → tablica (raw_name, tip, „postoji?"), po retku uređivo ime + akcija (import/merge/skip), „Uvezi". Sve `textContent`/`fetch` same-origin, po uzoru na `templates_org.py`. (Minimalan vizual — funkcija prije stila.)

`_NAV` u `templates_ui.py`: dodaj `("klijenti-uvoz", "/ui/klijenti-uvoz", "Uvoz klijenata")`.

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/web/api.py ragspine/web/templates_uvoz.py ragspine/web/templates_ui.py tests/test_discover_api.py
git commit -m "feat(discovery): discover/commit endpointi + ekran uvoza klijenata"
```

---

### Task 8: Orijentacijska kartica na dashboardu

**Files:**
- Modify: `ragspine/business/dashboard.py` (`home_data` +`orientation`), `ragspine/web/templates_ui.py` (dashboard render + JS)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `folder_scan.latest`, `notifications` (kind `folder_connected`).
- Produces: `dashboard.home_data(spine)` dobiva ključ `orientation`: `{folders:[{id,label,role,scan}], pending_actions:[...]}`.

- [ ] **Step 1: Failing test**

```python
# tests/test_dashboard.py
from ragspine.business import dashboard, folders, folder_scan

def test_home_data_has_orientation(spine, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "nas_root", str(tmp_path), raising=False)
    kl = tmp_path / "KLIJENTI"; (kl / "A").mkdir(parents=True)
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    folder_scan.scan(spine, cfg, fid)
    data = dashboard.home_data(spine)
    assert "orientation" in data
    assert data["orientation"]["folders"][0]["role"] == "klijenti"
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement** — u `dashboard.home_data` dodaj:

```python
    folders_rows = spine.read().execute(
        "SELECT f.id, f.label, f.role, f.path, s.n_subdirs, s.n_docs, s.n_pdf_no_text "
        "FROM folders f LEFT JOIN folder_scan s ON s.folder_id=f.id WHERE f.enabled=1").fetchall()
    orientation = {"folders": [
        {"id": r["id"], "label": r["label"] or r["path"], "role": r["role"],
         "scan": {"n_subdirs": r["n_subdirs"], "n_docs": r["n_docs"], "n_pdf_no_text": r["n_pdf_no_text"]}}
        for r in folders_rows]}
    # (dodaj u return dict pod ključ "orientation")
```

Dashboard template: kartica „Orijentacija" koja iscrta spojene mape + brzu akciju
„Uvezi klijente" (link `/ui/klijenti-uvoz`) za klijenti-mape i „Skeniraj sad"
(`POST /folders/{id}/scan`). `textContent`, `script_json`.

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git add ragspine/business/dashboard.py ragspine/web/templates_ui.py tests/test_dashboard.py
git commit -m "feat(dashboard): orijentacijska kartica spojenih mapa"
```

---

### Task 9: Puna suita + smoke pravog servera

**Files:** —

- [ ] **Step 1:** `python -m pytest -q` → sve zeleno (≥ prethodni broj + novi).
- [ ] **Step 2:** Smoke: podigni `ragspine serve` u pozadini, `curl -s localhost:$PORT/login` → 200; ugasi. (Ne blokira suitu; ručni check.)
- [ ] **Step 3: Commit** (ako je bilo popravaka)

```bash
git commit -am "test: A+B puna suita zelena"
```

---

## Self-Review (autor plana)
- **Spec coverage:** A (scan+obavijest+napomene) → Task 3,4,5,8. B (discover+pregled+commit+veza) → Task 6,7. UI sidebar+dashboard → Task 1,8. SKENER uloga → Task 2. Sve pokriveno.
- **Placeholderi:** nema TBD/„handle edge cases"; svaki task ima pravi test+kod.
- **Tipovi:** `discover`→`match_id`/`raw_name`/`guessed_type` konzistentni Task 6↔7; `folder_scan.scan`→`n_subdirs/n_docs/n_pdf/n_pdf_no_text` konzistentni Task 3↔4↔8.
- **Napomena:** dokument↔klijent veza je preko `clients.nas_folder` (Task 6); puni ingest/indeks tih dokumenata je komad C, izvan ovog plana (spec to eksplicitno kaže).
