# Windows kompatibilnost — poznate rupe (backlog)

CI (`.github/workflows/ci.yml`) zasad vrti samo Linux jer dev + dogfood teku na
Piju. Windows matrica je privremeno isključena — GitHub Actions ju je pokrenuo
(run 30831889011) i uhvatio 4 prave cross-OS rupe koje treba riješiti **prije
bilo kakvog Windows deploya** (produkcijski PC ureda može biti Windows + SMB NAS).

## Uhvaćeno na windows-latest / Python 3.11

1. **UnicodeEncodeError na hrvatskim znakovima** — `tests/test_folder_sync.py`
   (4 testa). `Č` (Č) puca s cp1252 (Windows default). Uzrok: negdje se
   piše/čita tekst bez `encoding="utf-8"`. **Fix:** svaki `open()`/`read_text`/
   `write_text` s tekstom mora imati `encoding="utf-8"`; provjeriti i subprocess
   stdout dekodiranje. Grep: `open(` bez encoding u ragspine/.

2. **Path separator `\` vs `/`** — `tests/test_onboarding.py`
   (`klijenti\\1_pekara-mlinar` ≠ `klijenti/1_pekara-mlinar`). NAS/folder putevi
   se grade s `/`. **Fix:** koristiti `Path`/`os.path.join` i usporedbe
   normalizirati, ili u testu usporediti preko `Path`.

3. **"Paths don't have the same drive"** — `tests/test_sop_images.py`
   (escape-guard). `os.path.relpath`/`commonpath` puca kad su tmp i cilj na
   različitim diskovima (C: vs D:) na Windowsu. **Fix:** guard obuhvatiti
   try/except ValueError → tretirati kao escape (odbij), ili koristiti
   `Path.is_relative_to`.

4. **subprocess kill tree timing** — `tests/test_subproc.py::test_timeout_kills_tree`
   (uzelo ~60s umjesto <10s). Kill procesnog stabla na Windowsu ne ide preko
   POSIX signala. **Fix:** na Windowsu koristiti `CREATE_NEW_PROCESS_GROUP` +
   `taskkill /T`, ili `psutil` za rekurzivni kill.

Cookie-Secure test (`test_api_auth`) je već popravljen (https base_url) i nije
Windows-specifičan nego starlette 1.3+ ponašanje.
