# Sljedeća TUI grana — ostatak face-lifta (spec: docs/e2e-nalazi-2026-08-06.md)

Napravljeno u grani tui-facelift: curses jezgra (radiolist/checklist + ESC
dekodiranje + fallback), tablica modela (Disk stupac, rangirane namjene,
kontekst stroja), radiolist izbori na stranici 1, getpass lozinka,
windows-curses dep.

Ostaje (redoslijedom prioriteta iz nalaza):
1. Folder picker (stranica 5): TUI preglednik mapa — diskovi + UNC upis
   jednom pa browsanje; Enter=uđi, Razmak/prvi red=odaberi, ESC/..=gore;
   GetDriveType upozorenje SAMO za DRIVE_REMOTE.
2. Živi izlaz podprocesa: winget najava veličine + progress; ollama pull
   poštovati \r (čitanje sirovog streama).
3. PATH refresh bez restarta (registry HKLM/HKCU) + poznate lokacije probe.
4. Tesseract auto-install: hrv.traineddata download, user PATH, "već
   instalirano" poruka.
5. install.ps1 uskladba (bez pitanja operatera, uputa na atlas setup).
6. Prečac na radnoj površini (sve platforme).
7. Cert bootstrap stranica (http://IP:8080/postavi) + prijateljsko ime
   (fritz.box/mDNS) u SAN + doslovna uputa za radnike na stranici 6/6.
8. Pull s kvant sufiksom (llmfit kvant ≠ registry default) + usporedba
   stvarne veličine.
9. bge-m3 feature-detect / fastembed upgrade; MODEL_CATALOG trim na 2-3
   fallback modela; kozmetika (fitz warning, miješane kose crte, getpass
   za `atlas auth add`).
