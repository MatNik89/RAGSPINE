# Rename RAGSPINE → ATLAS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Proizvod, paket, CLI, env i poruke nose ime ATLAS; stare instalacije rade preko aliasa/fallbacka.

**Architecture:** Blanket sed rename po kodu (paket, testovi, pyproject) kao prvi atomski korak — nakon njega kod nema starog imena. Kompatibilnosni sloj (env aliasi, data dir/db fallback, CLI alias) dodaje se ZATIM, s markerom `compat` u svakom retku koji namjerno spominje staro ime. Trajni audit test drži stanje.

**Tech Stack:** Python 3.11+, pytest, sed. Bez novih ovisnosti.

## Global Constraints

- Hrvatski latinica s dijakriticima; NIKAD ćirilica.
- Bez novih ovisnosti.
- Testovi bez mreže/stdina/pravih subprocessa.
- Puni suite u prvom planu (foreground) prije svakog commita zadatka.
- Hrvatske konvencionalne commit poruke + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Povijesni docs (`docs/superpowers/**`, `docs/e2e-nalazi-2026-08-06.md`, `docs/NEXT_SESSION_SECURITY.md`) se NE diraju.
- GitHub repo ime se NE dira.

---

### Task 1: Mehanički rename — paket, importi, testovi, pyproject

**Files:**
- Rename: `ragspine/` → `atlas/` (git mv)
- Modify: svi `atlas/**/*.py`, `tests/**/*.py`, `pyproject.toml` (sed)

**Interfaces:**
- Produces: paket `atlas`, CLI `atlas` + alias `ragspine`, sve env reference u kodu `ATLAS_*`, cookie `atlas_token`, `Server: ATLAS`, `User-Agent: ATLAS/1.0` (sve to sed odradi sam jer su stringovi u kodu).

- [ ] **Step 1: git mv + sed**

```bash
git mv ragspine atlas
rm -rf ragspine.egg-info
grep -rl -i ragspine atlas tests pyproject.toml | while read f; do
  sed -i 's/ragspine/atlas/g; s/RAGSPINE/ATLAS/g; s/Ragspine/Atlas/g' "$f"
done
```

- [ ] **Step 2: pyproject — CLI alias za staro ime**

U `pyproject.toml` (nakon seda `atlas = "atlas.__main__:main"`), dodaj alias redak ispod:

```toml
[project.scripts]
atlas = "atlas.__main__:main"
ragspine = "atlas.__main__:main"  # compat: stari CLI, do v2
```

- [ ] **Step 3: reinstall + kontrola**

```bash
pip install -e . --quiet 2>&1 | tail -1
grep -ri ragspine atlas/ | grep -v compat   # očekuj: prazno
```

- [ ] **Step 4: puni suite**

Run: `python -m pytest -q` (foreground)
Expected: 1147 passed, 1 skipped (isti broj kao prije)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rename paketa ragspine → atlas — importi, testovi, pyproject s CLI aliasom"
```

---

### Task 2: Kompatibilnosni sloj — env aliasi + data dir/db fallback

**Files:**
- Modify: `atlas/config.py` (cijeli from_env kroz helper)
- Modify: `atlas/ops/preflight.py:90,379` (goli `~/.atlas` fallback kroz helper)
- Modify: `atlas/__main__.py:89,262`, `atlas/rag/embed.py:45`, `atlas/ops/wizard.py:469` (direktna env čitanja kroz helper)
- Test: `tests/test_config_compat.py` (novi)

**Interfaces:**
- Produces: `atlas.config._env(name: str, default: str = "") -> str` — čita `ATLAS_<name>` pa `RAGSPINE_<name>` pa default; `atlas.config.default_data_dir() -> str` — `~/.atlas` s fallbackom na postojeći `~/.ragspine`.

- [ ] **Step 1: failing testovi**

`tests/test_config_compat.py`:

```python
"""Compat sloj renamea: RAGSPINE_* env aliasi + ~/.ragspine fallback."""  # compat: ragspine
import os
from pathlib import Path

from atlas import config


def test_env_atlas_ima_prednost(monkeypatch):
    monkeypatch.setenv("ATLAS_HOST", "1.2.3.4")
    monkeypatch.setenv("RAGSPINE_HOST", "5.6.7.8")  # compat: ragspine
    assert config._env("HOST", "x") == "1.2.3.4"


def test_env_stari_alias_radi(monkeypatch):
    monkeypatch.delenv("ATLAS_HOST", raising=False)
    monkeypatch.setenv("RAGSPINE_HOST", "5.6.7.8")  # compat: ragspine
    assert config._env("HOST", "x") == "5.6.7.8"


def test_env_default(monkeypatch):
    monkeypatch.delenv("ATLAS_HOST", raising=False)
    monkeypatch.delenv("RAGSPINE_HOST", raising=False)  # compat: ragspine
    assert config._env("HOST", "x") == "x"


def test_data_dir_fallback_na_stari(monkeypatch, tmp_path):
    """~/.atlas ne postoji, ~/.ragspine postoji → koristi stari."""  # compat: ragspine
    legacy = tmp_path / ".ragspine"  # compat: ragspine
    legacy.mkdir()
    monkeypatch.setattr(config, "_home", lambda: str(tmp_path))
    assert config.default_data_dir() == str(legacy)


def test_data_dir_novi_kad_nema_starog(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_home", lambda: str(tmp_path))
    assert config.default_data_dir() == str(tmp_path / ".atlas")


def test_data_dir_novi_ima_prednost(monkeypatch, tmp_path):
    (tmp_path / ".atlas").mkdir()
    (tmp_path / ".ragspine").mkdir()  # compat: ragspine
    monkeypatch.setattr(config, "_home", lambda: str(tmp_path))
    assert config.default_data_dir() == str(tmp_path / ".atlas")


def test_db_fallback_na_stari(monkeypatch, tmp_path):
    """Postojeći ragspine.db u data diru se koristi; inače atlas.db."""  # compat: ragspine
    for k in ("ATLAS_DATA_DIR", "ATLAS_DB_PATH", "ATLAS_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.replace("ATLAS", "RAGSPINE"), raising=False)  # compat: ragspine
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    (tmp_path / "ragspine.db").touch()  # compat: ragspine
    cfg = config.Config.from_env()
    assert cfg.db_path == str(tmp_path / "ragspine.db")  # compat: ragspine


def test_db_novi_default(monkeypatch, tmp_path):
    for k in ("ATLAS_DATA_DIR", "ATLAS_DB_PATH", "ATLAS_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.replace("ATLAS", "RAGSPINE"), raising=False)  # compat: ragspine
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    cfg = config.Config.from_env()
    assert cfg.db_path == str(tmp_path / "atlas.db")
```

- [ ] **Step 2: run — očekuj FAIL** (`_env` ne postoji)

Run: `python -m pytest tests/test_config_compat.py -q`

- [ ] **Step 3: implementacija u `atlas/config.py`**

```python
def _home() -> str:
    return os.path.expanduser("~")


def _env(name: str, default: str = "") -> str:
    """ATLAS_<name> primarno; RAGSPINE_<name> je trajni alias."""
    v = os.environ.get(f"ATLAS_{name}")
    if v is None:
        v = os.environ.get(f"RAGSPINE_{name}")  # compat: ragspine env alias
    return default if v is None else v


def default_data_dir() -> str:
    """~/.atlas; ako ne postoji a stari ~/.ragspine postoji — koristi stari."""
    new = os.path.join(_home(), ".atlas")
    legacy = os.path.join(_home(), ".ragspine")  # compat: ragspine data dir
    if not os.path.exists(new) and os.path.isdir(legacy):
        return legacy
    return new
```

`from_env`: sva `e("ATLAS_X", d)` čitanja zamijeni s `_env("X", d)`;
`data_dir = os.path.expanduser(_env("DATA_DIR") or default_data_dir())`;
db default:

```python
        default_db = Path(data_dir) / "atlas.db"
        legacy_db = Path(data_dir) / "ragspine.db"  # compat: ragspine db ime
        if not default_db.exists() and legacy_db.exists():
            default_db = legacy_db
```

pa `db_path=_env("DB_PATH", str(default_db))`.

Ostala mjesta:
- `atlas/ops/preflight.py:90,379`: `os.path.expanduser("~/.atlas")` → `config.default_data_dir()` (import na vrhu).
- `atlas/__main__.py:89`: `os.environ.get("ATLAS_PASS")` → `config._env("PASS") or None`; `:262`: `config._env("USER", "sustav")`.
- `atlas/rag/embed.py:45`: `config._env("TEST_EMBED") != "1"` (pazi na obrnutu logiku — original je `os.environ.get(...) != "1"`, `_env` default "" daje isto ponašanje).
- `atlas/ops/wizard.py:469`: `config._env("MOUNT_ROOTS", "")`.

- [ ] **Step 4: run testova + puni suite**

Run: `python -m pytest tests/test_config_compat.py -q` → PASS, pa `python -m pytest -q` → 1155+ passed.

- [ ] **Step 5: Commit**

```bash
git add atlas/config.py atlas/ops/preflight.py atlas/__main__.py atlas/rag/embed.py atlas/ops/wizard.py tests/test_config_compat.py
git commit -m "feat(config): ATLAS_* env s trajnim RAGSPINE_* aliasom + fallback na ~/.ragspine i ragspine.db"
```

---

### Task 3: Vidljivi tekstovi — README, install skripte, extension, aktivni docs, upute za repo

**Files:**
- Modify: `README.md`, `install.sh`, `install.ps1`, `extension/manifest.json`, `extension/background.js`, `extension/popup.html`
- Modify: `docs/DEPLOY_URED.md`, `docs/WINDOWS_COMPAT.md`, `docs/F_WIZARD.md`, `docs/D_FOLDER_ARCHITECTURE.md`, `docs/E_DEVICES.md`, `docs/G_PRACENJE.md`, `docs/C2_DOC_TYPES.md`, `docs/C3_EXTRACTION.md` (samo oni koji spominju ime)
- Create: `docs/RENAME_REPO.md`

**Interfaces:**
- Consumes: CLI `atlas` (Task 1), env `ATLAS_*` (Task 2).

- [ ] **Step 1: sed po vidljivim datotekama**

```bash
sed -i 's/ragspine/atlas/g; s/RAGSPINE/ATLAS/g; s/Ragspine/Atlas/g' \
  README.md install.sh install.ps1 extension/manifest.json \
  extension/background.js extension/popup.html \
  docs/DEPLOY_URED.md docs/WINDOWS_COMPAT.md docs/F_WIZARD.md \
  docs/D_FOLDER_ARCHITECTURE.md docs/E_DEVICES.md docs/G_PRACENJE.md \
  docs/C2_DOC_TYPES.md docs/C3_EXTRACTION.md
```

Zatim RUČNO pregledaj diff (`git diff`) — vrati/popravi:
- git remote / GitHub URL-ove: ostaju `RAGSPINE` dok korisnik ne preimenuje repo; označi `<!-- compat: URL do repo renamea -->` (markdown) ili `# compat` (skripte).
- README prvi naslov: `# ATLAS` + redak „*(ranije RAGSPINE)*" `<!-- compat: staro ime -->`.
- install skripte: provjeri da klon-mapa/venv logika i dalje odgovara stvarnom checkoutu (skripte rade iz lokalne mape — ime mape NE hardkodirati).
- `RAGSPINE-serve` u docs → `ATLAS-serve`, ali napomena u DEPLOY_URED da postojeći task na starim strojevima nosi staro ime.

- [ ] **Step 2: docs/RENAME_REPO.md**

```markdown
# Rename GitHub repoa RAGSPINE → ATLAS (ručno, vlasnik)

1. GitHub → repo Settings → Repository name → `ATLAS` → Rename.
   (GitHub postavlja redirect sa starog imena, klonovi rade dalje.)
2. Na svakom stroju s klonom:
   `git remote set-url origin https://github.com/MatNik89/ATLAS.git`
3. U repou zatim počisti `compat: URL` retke (README, install skripte).
```

- [ ] **Step 3: puni suite** (`python -m pytest -q`) — bez promjene broja.

- [ ] **Step 4: Commit**

```bash
git add README.md install.sh install.ps1 extension/ docs/
git commit -m "docs: ATLAS ime u README, install skriptama, extensionu i aktivnim docs + upute za repo rename"
```

---

### Task 4: Trajni audit test + finalni grep audit

**Files:**
- Test: `tests/test_rename_audit.py` (novi)

**Interfaces:**
- Consumes: sve prethodne taskove (kod bez starog imena osim `compat` redaka).

- [ ] **Step 1: napiši test**

```python
"""Trajni audit: staro ime ne smije natrag u KOD (docs povijest smije)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Kod = paket, testovi, install skripte, pyproject, extension.
CODE_TARGETS = ["atlas", "tests", "extension", "install.sh", "install.ps1",
                "pyproject.toml"]
STARO = re.compile("rag" "spine", re.I)  # razlomljeno da sam sebe ne ulovi
PRESKOCI_DIJELOVE = {"__pycache__", ".pytest_cache"}


def _datoteke():
    for top in CODE_TARGETS:
        p = ROOT / top
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in p.rglob("*"):
                if (f.is_file() and not f.suffix == ".pyc"
                        and not PRESKOCI_DIJELOVE & set(f.parts)
                        and "egg-info" not in str(f)):
                    yield f


def test_staro_ime_nije_u_kodu_bez_compat_markera():
    losi = []
    for f in _datoteke():
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if STARO.search(line) and "compat" not in line.lower():
                losi.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not losi, ("Staro ime u kodu bez 'compat' markera:\n"
                      + "\n".join(losi))
```

- [ ] **Step 2: run — mora odmah PASS** (ako padne, popravi nalaze: ili pravi rename ili `compat` marker ako je namjerno)

Run: `python -m pytest tests/test_rename_audit.py -q`

- [ ] **Step 3: finalni grep audit cijelog repoa**

```bash
grep -ri ragspine . --exclude-dir=.git --exclude-dir=.kilo \
  --exclude-dir=__pycache__ --exclude-dir=.pytest_cache \
  --exclude-dir=superpowers -l
```

Dozvoljeni pogoci SAMO: `compat` retci (kod), `docs/e2e-nalazi-2026-08-06.md`,
`docs/NEXT_SESSION_SECURITY.md`, `docs/RENAME_REPO.md`, `AGENTS.md` (Kilo,
untracked), README/install URL-ovi s compat markerom. Sve drugo = popravi.

- [ ] **Step 4: puni suite** (`python -m pytest -q`)

- [ ] **Step 5: Commit**

```bash
git add tests/test_rename_audit.py
git commit -m "test: trajni audit protiv povratka starog imena u kod"
```
