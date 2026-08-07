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

Napravljeno u grani wizard-bez-mapa (11c0ee3, korisnikova odluka
2026-08-07): stranica mapa VAN iz wizarda — wizard je 5 stranica ("od
nule do prijavljenog admina"), mape isključivo kroz web Postavke →
Mrežne mape; folder_picker obrisan; dashboard prazno stanje s linkom na
/ui/mape; legacy resume (stare baze stage 4/5) kompatibilan.

Napravljeno u grani cert-bootstrap (41e2cde): prijateljska imena u SAN-u
(certs.friendly_names + verified_display_host usklađen sa SAN-om
POSTOJEĆEG certa), HTTP bootstrap server uz serve
(atlas/web/bootstrap_http.py — /postavi stranica, /postavi-vezu.bat,
/cert.pem; ATLAS_BOOTSTRAP_PORT, "0"=off), doslovna uputa za radnike na
stranici 5/5, ispravljena uputa "trust na klijentima".

Napravljeno u grani uv-install (d0d7206): uv primarni put u
install.ps1/sh (winget/curl bootstrap, uv venv --python 3.12 = kraj
Find-Python cirkusa; pip fallback netaknut; ATLAS_NO_UV=1 izlaz na oba
OS-a), install.sh usklađen s wizardom (obrisan headless seed + operater
sekcija), README ispravljen.

TUI backlog PRAZAN — sve E2E TUI stavke odrađene. Ostaje šire:
- BRISANJE Nicka + čista E2E proba (čeka korisnikov "kreni s probom").
- Deferred sitnice iz reviewa (dolje) — uzeti usput.
- Follow-up ideja iz cert reviewa: SHA256 fingerprint na /postavi
  stranici (helper postoji) — MITM otvrdnjavanje bootstrapa.

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
