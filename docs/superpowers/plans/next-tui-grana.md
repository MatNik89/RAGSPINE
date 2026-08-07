# Sljedeća TUI grana — ostatak face-lifta (spec: docs/e2e-nalazi-2026-08-06.md)

Napravljeno u grani tui-facelift (076a725): curses jezgra (radiolist/
checklist + ESC dekodiranje + fallback), tablica modela (Disk stupac,
rangirane namjene, kontekst stroja), radiolist izbori na stranici 1,
getpass lozinka, windows-curses dep.

Napravljeno u grani tui-facelift-2 (a414e86): folder picker (stranica 5,
DRIVE_REMOTE-only upozorenje), živi izlaz podprocesa (subproc.run_streaming
s \r; winget najava veličine; ollama pull svakih 10 % / in-place na TTY),
PATH refresh bez restarta + poznate lokacije (atlas/ops/winpath.py),
Tesseract auto-install s hrv+eng traineddata i TESSDATA_PREFIX fallbackom,
"već instalirano" prepoznavanje, install.ps1 uskladba, OCR runtime kroz
find_binary.

Ostaje (redoslijedom prioriteta iz nalaza):
1. Prečac na radnoj površini (sve platforme): Windows .lnk
   (msedge/chrome --app=URL) + Start Menu; Linux .desktop; macOS .webloc.
2. Cert bootstrap stranica (http://IP:8080/postavi) + prijateljsko ime
   (fritz.box/mDNS) u SAN + doslovna uputa za radnike na stranici 6/6 +
   fix upute "ragspine trust na klijentima" (CLI tamo ne postoji). Veći
   komad — može zasebna grana.
3. Pull s kvant sufiksom (llmfit kvant ≠ registry default) + usporedba
   stvarne veličine nakon pulla.
4. bge-m3 feature-detect / fastembed upgrade; MODEL_CATALOG trim na 2-3
   fallback modela; kozmetika (fitz warning, miješane kose crte, getpass
   za `atlas auth add`).

Parkirano iz reviewa tui-facelift-2 (uzeti usput):
- LocalService servis (winsvc) ne vidi HKCU env (TESSDATA_PREFIX/user
  PATH) — riješiti uz WinSW/NSSM wrapper fazu.
- run_streaming: taskkill-fail grana može ostaviti unuka s pipeom (read
  blokira) — bounded-drain backstop kao u run_isolated.
- tui.prompt_yes_no/prompt_text ne hvataju EOFError (pre-existing).
- Emoji ljust pomak u tablici modela (wcwidth ili ⭐ na kraju retka).
