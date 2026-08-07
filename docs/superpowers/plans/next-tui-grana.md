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

Napravljeno u grani tui-facelift-3 (621fb9e): prečac na radnoj površini
(atlas/ops/shortcut.py — .lnk app-prozor s .url fallbackom + Start Menu,
.desktop, .webloc; automatski iz launch_nowa), pull s kvant sufiksom
(quant_tags kandidati → goli tag + ⚠; ollama_model_size usporedba;
sprema se stvarni tag), kozmetika (normpath data_dir, prigušeni fitz/
fastembed warninzi, embed.supports guard — bge-m3 potvrđeno nepodržan na
fastembed 0.8.0 pa se više ne nudi). MODEL_CATALOG stavka otpala (ne
postoji u kodu); getpass za auth add već postojao.

Ostaje:
1. Cert bootstrap stranica (http://IP:8080/postavi) + prijateljsko ime
   (fritz.box/mDNS) u SAN + doslovna uputa za radnike na stranici 6/6 +
   fix upute "trust na klijentima" (CLI tamo ne postoji). Zasebna grana.

Parkirano iz reviewa tui-facelift-2 i -3 (uzeti usput):
- Promjena prečac varijante (.lnk ↔ .url) ostavlja stari artefakt na
  Desktopu; GNOME "Allow Launching" confirm za .desktop.
- PS skript bez escapea apostrofa u putanji (degradira na .url).
- LocalService servis (winsvc) ne vidi HKCU env (TESSDATA_PREFIX/user
  PATH) — riješiti uz WinSW/NSSM wrapper fazu.
- run_streaming: taskkill-fail grana može ostaviti unuka s pipeom (read
  blokira) — bounded-drain backstop kao u run_isolated.
- tui.prompt_yes_no/prompt_text ne hvataju EOFError (pre-existing).
- Emoji ljust pomak u tablici modela (wcwidth ili ⭐ na kraju retka).
