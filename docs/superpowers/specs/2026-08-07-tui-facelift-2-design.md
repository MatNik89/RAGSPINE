# TUI face-lift 2 — dizajn (2026-08-07)

## Cilj

Ostatak TUI dorada po prioritetu iz docs/superpowers/plans/next-tui-grana.md,
stavke 1-5: folder picker, živi izlaz podprocesa, PATH refresh bez restarta,
Tesseract auto-install s hrv paketom, install.ps1 uskladba.

## Opseg

1. **Folder picker (stranica 5)** — novi modul `atlas/ops/folder_picker.py`:
   - `pick_folder(*, input_fn, out, start=None) -> str | None` — TUI
     preglednik mapa nad `tui_curses.radiolist` (curses na TTY-ju,
     numerirani fallback u testovima)
   - početni ekran: diskovi (Windows: A:-Z: koji postoje; POSIX: `/` +
     `~`), "Mrežna lokacija (\\\\server — upis jednom, pa browsanje)",
     "Ručni upis putanje (napredno)"
   - u mapi: red 0 = `[✓ ODABERI OVU MAPU]`, red 1 = `..` (razina gore),
     zatim podmape abecedno; Enter na mapi = uđi (neograničena dubina),
     Enter na ✓ = odabir, ESC = razina gore / na vrhu izlaz (None);
     naslov = trenutna putanja, header = legenda tipki
   - nedostupne podmape (PermissionError) se preskaču bez pada
   - `page_mape`: picker umjesto tipkanja; "Ručni upis" ostaje; postojeće
     UNC provjere/registracija na odabranom rezultatu NE mijenjaju se
   - **upozorenje o slovu pogona preusko cilja (E2E nalaz)**: novi
     `_drive_warn(path)` — upozori SAMO za DRIVE_REMOTE (GetDriveTypeW==4,
     ctypes, Windows); lokalni fiksni disk bez ⚠; ne-Windows: nikad
2. **Živi izlaz podprocesa**:
   - `atlas/core/subproc.run_streaming(cmd, *, timeout, out, popen)` —
     čita sirovi stream (stdout+stderr spojeno), poštuje `\r` (osvježavanje
     retka u mjestu na pravom TTY-ju; injektirani out dobiva retke),
     timeout preko threading.Timer + postojeći kill-stablo obrazac
   - `install_via_winget`: najava veličine/trajanja (katalog: Ollama
     ~700 MB, Tesseract ~60 MB) + `run_streaming` umjesto run_isolated
     (kraj mrtvog ekrana)
   - `ollama_pull`: na pravom TTY-ju (out is print) postotak se osvježava
     u mjestu (`\r`); s injektiranim out (testovi/web) svakih 10 % novi
     redak umjesto svakih 1 % (manje spama)
3. **PATH refresh bez restarta** — novi modul `atlas/ops/winpath.py`:
   - `_merge_path(current, machine, user) -> str` (čisto, unit-testabilno:
     spoji bez duplikata, redoslijed machine+user+current)
   - `refresh_path_from_registry() -> bool` — Windows: winreg HKLM/HKCU
     Environment → os.environ["PATH"]; drugdje no-op False
   - `find_binary(key) -> str | None` — shutil.which pa poznate lokacije
     (`C:\Program Files\Tesseract-OCR\tesseract.exe`,
     `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`)
   - `persist_user_env(name, value)` — winreg HKCU\Environment upis (NE
     setx — truncira na 1024 znaka) + WM_SETTINGCHANGE broadcast; drugdje
     no-op. Za PATH append i TESSDATA_PREFIX.
   - integracija: `install_via_winget` nakon installa refresha PATH i
     proba poznate lokacije ("restartaj terminal" poruka nestaje);
     `requirements()` tesseract/ollama detekcija kroz `find_binary`
4. **Tesseract auto-install dorada (KRITIČNO iz E2E)**:
   - "već instalirano": `install_via_winget` prvo `find_binary` — ako
     postoji, poruka "već instalirano" i preskoči winget (ali za
     tesseract svejedno odradi jezike/PATH korake ispod)
   - `ensure_traineddata(lang="hrv", *, out, urlopen)` u preflightu:
     skini `https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/
     hrv.traineddata` u `<exe_dir>/tessdata`; PermissionError →
     `<data_dir>/tessdata` + `TESSDATA_PREFIX` u os.environ + persist
     (winpath). Idempotentno (postojeća datoteka = ništa). urlopen
     injektabilan — testovi bez mreže.
   - PATH: ako je exe nađen na poznatoj lokaciji izvan PATH-a, dodaj dir
     u os.environ PATH + persist_user_env
   - poziv: unutar `install_via_winget("tesseract", ...)` nakon (pre)installa
5. **install.ps1 uskladba (E2E nalaz)**: makni pitanje o operateru i
   headless `atlas setup` seed poziv; ostaje priprema okoline
   (Python/venv/pip/embedding); završni tekst upućuje na
   `.\.venv\Scripts\atlas.exe setup` (wizard; HTTPS 8443), ne `serve`+8400.

## Izvan opsega (ostaje u next-tui-grana.md)

Prečac na desktopu (6), cert bootstrap + prijateljsko ime (7 — zasebna
grana), pull s kvant sufiksom (8), bge-m3/MODEL_CATALOG/kozmetika (9).

## Testabilnost

- folder_picker: fallback put kroz input_fn (kao tui_curses); listanje
  mapa nad tmp_path; _drive_warn s mockanim ctypes pozivom
- run_streaming: lažni popen (BytesIO stdout), bez pravih subprocessa
- winpath: _merge_path čisto; registry/broadcast tanki Windows-only sloj
  (guard `os.name != "nt"` → no-op, testira se guard)
- ensure_traineddata: injektirani urlopen + tmp_path; PermissionError
  grana simulirana read-only ciljem/monkeypatchom
- install_via_winget tok: monkeypatch find_binary/run_streaming

## Rizici

- winreg upis u HKCU\Environment: krivi tip (REG_SZ vs REG_EXPAND_SZ) —
  čitaj postojeći tip pa piši isti; PATH append nikad ne briše postojeće.
- Program Files upis bez elevacije pada — zato TESSDATA_PREFIX fallback.
- run_streaming na Windows konzoli: cp1250 — dekodiraj utf-8 errors=replace
  (postojeći obrazac iz run_isolated).
