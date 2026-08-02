# Mrežne mape — Faza 1 (Registar mapa + Mape UI) — plan

> **For agentic workers:** implementiraj TDD, task po task; svaki task = failing test → min. implementacija → zeleno → commit.

**Cilj:** RAGSPINE vidi montirane mrežne korijene, izlista podmape, korisnik svakoj mapi dodijeli ulogu (`zakoni`/`klijenti`/custom); sve read-only i putanjski scoped ispod `mount_roots`.

**Spec:** `docs/superpowers/specs/2026-08-03-mrezne-mape-pravna-baza-design.md`

## Global Constraints
- Sve putanje: `realpath` + `commonpath` ispod nekog `cfg.mount_roots`; izvan → `ValueError`/400. Read-only (nikad pisati).
- Svi endpointi `require_user_web` (bearer ili cookie); `/ui/*` browser stranice → 303 na `/login` bez auth.
- Bez SMB kredencijala u aplikaciji. Bez vanjskih resursa u UI-ju; `textContent` za API-podatke.
- SQL parametriziran. Nove kolone/tablice idempotentne (`CREATE TABLE IF NOT EXISTS`).

## Task 1: shema + config
**Files:** `ragspine/core/spine.py` (SCHEMA), `ragspine/config.py`, `tests/test_folders.py`
- `folders(id INTEGER PK, path TEXT UNIQUE, role TEXT, label TEXT, enabled INTEGER DEFAULT 1, added_by TEXT, added_at TEXT DEFAULT (datetime('now')))`.
- `Config.mount_roots: list[str]` iz `RAGSPINE_MOUNT_ROOTS` (comma-split, expanduser, realpath, drop prazne).
- Test: config parsira 2 korijena; tablica postoji.

## Task 2: business/folders.py — scoping + browse
**Files:** `ragspine/business/folders.py`, `tests/test_folders.py`
- `ROLES = ("zakoni", "klijenti", "ostalo")` (prijedlog; role je slobodan string).
- `_scoped(cfg, path) -> str`: `realpath(path)`; mora biti jednak nekom mount_root ili ispod njega (`commonpath([rp, root]) == root`); inače `ValueError`. Prazan `mount_roots` → sve odbij.
- `browse(cfg, path=None) -> dict`: ako `path` prazan → vrati `{"roots": mount_roots, "dirs": []}`; inače scoped, vrati `{"path": rp, "parent": <ili None ako je root>, "dirs": [imena podmapa]}`. Samo direktoriji; ne prati simlink van korijena (realpath hvata); ne postoji/nije dir → `ValueError`.
- Test: browse korijena; browse podmape; **path izvan mount_root → ValueError**; simlink koji vodi van → ValueError; nepostojeći → ValueError.

## Task 3: registar CRUD
**Files:** `ragspine/business/folders.py`, `tests/test_folders.py`
- `register(spine, cfg, path, role, label, user) -> dict`: `_scoped` + mora postojati i biti dir; `INSERT OR IGNORE`/`ON CONFLICT(path) DO UPDATE` (role/label); audit `folder_register`. Vrati red.
- `list_folders(spine) -> list[dict]`.
- `update(spine, id, role=None, label=None, enabled=None)`: mijenja proslijeđena polja; 404-sentinel (`ValueError`) ako id ne postoji.
- `remove(spine, id)`: DELETE (ne dira disk).
- Test: register scoped+exists; izvan korijena → ValueError; list; update role; remove; register nepostojeće putanje → ValueError.

## Task 4: endpointi
**Files:** `ragspine/web/api.py`, `tests/test_folders.py`
- `GET /folders/browse?path=` → `folders.browse`; `ValueError`→400.
- `GET /folders` → list.
- `POST /folders` (Body: path, role, label) → register; `ValueError`→400.
- `POST /folders/{id}` (Body: role?, label?, enabled?) → update; 404 ako nema.
- `DELETE /folders/{id}` → remove.
- Svi `require_user_web`. Test: bez auth → 401; browse izvan korijena → 400; CRUD kroz TestClient.

## Task 5: ekran /ui/mape
**Files:** `ragspine/web/templates_mape.py` (novo), `ragspine/web/api.py`, `tests/test_folders.py`
- `GET /ui/mape` → `mape_page()`; bez auth → 303 `/login`.
- Stranica (isti design-shell): lijevo browser (`/folders/browse`, klik ulazi/izlazi), desno forma dodjele uloge (select role + label + „Dodaj mapu"), tablica registriranih (putanja/uloga/uključena/makni). Sve `textContent`, bez vanjskih resursa.
- Nav link „Mape". Test: stranica ima `@font-face`, `/folders/browse`, „Mape", `innerHTML` odsutan.

## Faza 2 (skica, poslije Faze 1)
`folders_sync` job; autoritet-iz-putanje u ingestu; verzioniranje; freshness.

## Faza 3 (skica)
`rag/verify.py` višeprolazna petlja; 80% prag + obrazloženje; prikaz „Točnost: N%"; anti-yes-man prompt.
