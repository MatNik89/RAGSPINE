# TUI face-lift wizarda — dizajn (2026-08-07)

## Cilj

Setup wizard dobiva hermes-style curses UI: strelice + Enter umjesto tipkanja
brojeva, pun highlight retka, boje, tablica modela. Fallback bez cursesa
ostaje numerirani tekst (i jedini je put u testovima — bez TTY-ja).

## Opseg (po prioritetu iz session prompta — prevelik zalogaj za sve odjednom)

1. **Curses jezgra** — novi modul `atlas/ops/tui_curses.py` (uzor
   hermes-agent `hermes_cli/curses_ui.py`, bez fuzzy searcha — YAGNI):
   - `radiolist(...)` (jedan izbor) i `checklist(...)` (razmaknica toggle)
   - strelice ↑/↓ + j/k, Enter potvrda, ESC natrag — s razlikovanjem lone
     ESC vs escape-sekvenca strelica (CSI/SS3 dekodiranje + timeout 60 ms,
     split-sekvence preko SSH/tmux)
   - kursor red = puni highlight (bold+zeleno), opisi ispod stavki, naslov
     žuto, hint red prigušeno; hrvatski hintovi
   - flush stdin nakon cursesa (escape ostaci ne smiju curiti u input())
   - **fallback**: numerirani tekst kroz `input_fn`/`out` kad nema TTY-ja,
     nema cursesa, ili je `input_fn` injektiran (testovi) — ponašanje
     deterministicki testabilno bez mreže/stdina
2. **windows-curses** — JEDINA dopuštena nova ovisnost:
   `windows-curses>=2.3; sys_platform == "win32"` u pyproject dependencies.
   Bez njega (stara instalacija) wizard radi kroz fallback.
3. **Stranica 3 (model) — tablica** (izričit korisnikov zahtjev):
   - radiolist u TABLICI: stupci `Naziv | Param | Kvant | RAM | Disk |
     Brzina | Namjena`, redovi jedan ispod drugog, ↑/↓ + Enter
   - **Disk** stupac: llmfit ne daje veličinu na disku — procjena iz
     paramsa i kvantizacije (bits/weight tablica × params × 1.1 režija),
     prikaz "~X,X GB"
   - **Namjena** rangirana (1. najjača › 2. › 3.): vlastiti katalog namjena
     po obitelji modela (qwen/llama/phi/mistral/gemma/deepseek-r1...);
     llmfit use_case ostaje fallback za nepoznate obitelji
   - iznad tablice: ukupno slobodno RAM i diska stroja (kontekst za stupce)
   - fit legenda: 🟢 komotno / 🟡 tijesno — odnosi se na RAM, ne disk
4. **Stranica 1 (preduvjeti)**: prikaz ostaje glyph lista (✓/⚠/✗ boje u
   cursesu ionako ne trebaju — ispis je izvan menija); izbori postaju
   radiolist ("Provjeri ponovno" / "Auto-instaliraj: X" / "Nastavi bez" /
   "Prekini") umjesto d/n tipkanja; winget ponuda i dalje Windows-only.
5. **getpass za lozinku** (stranica 2, VAŽNO s probe): skriveni unos oba
   polja kad je pravi TTY; fallback vidljivi unos kroz `input_fn`
   (testovi, ne-TTY). Ide u `tui.prompt_password`.

## Eksplicitno IZVAN opsega (sljedeća grana — docs/superpowers/plans/next-tui-grana.md)

Folder picker (str. 5), živi progress (winget/ollama pull `\r`), PATH
refresh iz registryja, Tesseract auto-install s hrv paketom, install.ps1
uskladba, prečac na desktopu, cert bootstrap stranica + prijateljsko ime u
SAN-u, pull s kvant sufiksom, bge-m3 feature-detect, MODEL_CATALOG trim,
kozmetika (fitz warning, miješane kose crte). Sve već specificirano u
docs/e2e-nalazi-2026-08-06.md.

## Arhitektura

- `atlas/ops/tui_curses.py` — čisti UI modul, bez ovisnosti o wizardu:
  - `NAV_UP/DOWN/SELECT/TOGGLE/CANCEL/NONE` + `_decode_menu_key(stdscr, key)`
  - `_run_menu(...)` zajednički event loop (ne-TTY guard, boje, scroll)
  - `radiolist(title, items, *, selected=0, descriptions=None, header="",
    input_fn=input, out=print) -> int | None` (None = ESC/odustao)
  - `checklist(title, items, selected, *, input_fn=input, out=print) -> set[int] | None`
  - `_use_curses(input_fn) -> bool`: curses import OK ∧ stdin TTY ∧
    `input_fn is builtins.input` — SVAKI injektirani input_fn (testovi,
    web-bridge) ide fallbackom
  - numerirani fallbacki (radio: broj+Enter; checklist: toggle brojem,
    Enter kraj) — hrvatski
- `atlas/ops/tui.py` — dobiva `prompt_password(question, *, input_fn=input,
  out=print) -> str`: getpass na TTY-ju s builtin input_fn, inače input_fn
- `atlas/ops/preflight.py` — dobiva `disk_free_gb(path=".") -> float`
  (shutil.disk_usage; ako već postoji ekvivalent, koristi njega)
- `atlas/ops/model_table.py` — novi mali modul: `disk_gb(params, quant)`,
  `namjene(ollama_name, use_case) -> str` (rangirano "chat › sažimanje"),
  `table_rows(rows) -> list[str]` (poravnati stupci) — čisto, unit-testabilno
- `atlas/ops/wizard.py` — stranice 1 i 3 prelaze na radiolist; potpisi
  stranica (spine, cfg, input_fn, out) se NE mijenjaju; postojeći testovi
  stranica se prilagođavaju fallback dijalogu (numerirani unos)

## Ponašanje na starim testovima

Testovi nemaju TTY i injektiraju input_fn ⇒ uvijek fallback put; curses
grane se testiraju unit testovima `_decode_menu_key` s lažnim stdscr-om
(skriptirani getch nizovi). Bez mreže/stdina/subprocessa.

## Rizici

- curses na Windowsu bez windows-curses → ImportError → fallback (guard).
- ESC vs strelica: timeout 60 ms na sporim PTY (SSH) — port hermes rješenja.
- Tablica šira od terminala: addnstr reže na max_x; minimalna širina
  se ne forsira (radije odrezan stupac Namjena nego crash).
