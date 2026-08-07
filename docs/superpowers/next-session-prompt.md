# Prompt za sljedeću sesiju (pripremljen 2026-08-07, poslije rename + TUI jezgre)

Kopiraj-zalijepi u novu sesiju:

---

Nastavi ATLAS rad (ex RAGSPINE — rename je GOTOV, ne diraj ga). Master @
076a725, sve mergeano i pushano, suite 1181 passed / 1 skipped, CI zelen.
Jučer/danas: rename RAGSPINE → ATLAS (env aliasi + ~/.ragspine fallback +
trajni audit test) i prva TUI grana (curses radiolist/checklist s
fallbackom, tablica modela s Disk stupcem i rangiranim namjenama, getpass,
windows-curses dep). Pročitaj prije početka:
docs/superpowers/plans/next-tui-grana.md (ostatak TUI-ja, po prioritetu) i
docs/e2e-nalazi-2026-08-06.md (detaljne specifikacije).

Radiš autonomno u loopu do kraja; javi se tek kad SVE završi. Proces: SDD
(superpowers:subagent-driven-development) na feature grani; merge = lokalni
merge u master + push origin master (moj ustaljeni izbor, ne pitaj); modeli:
haiku transkripcija / sonnet integracija+review / fable finalni review;
hrvatski latinica s dijakriticima — NIKAD ćirilična slova, NIGDJE; bez
novih depsa; testovi bez mreže/stdina/pravih subprocessa; puni suite U
PRVOM PLANU (subagentima izričito: jedan Bash poziv s timeoutom 600000, NE
background); hrvatske konvencionalne commit poruke s Co-Authored-By:
Claude Fable 5 footerom.

Zadaci redom:

1. Ostatak TUI-ja iz next-tui-grana.md (jedna ili dvije grane po procjeni;
   prioritet 1-4: folder picker, živi progress, PATH refresh, Tesseract
   hrv auto-install). Cert bootstrap (stavka 7) je veći komad — smije u
   zasebnu granu.

2. GitHub repo rename: JA radim ručno po docs/RENAME_REPO.md — ako remote
   još nosi staro ime, samo me podsjeti u izvještaju, ništa ne diraj.

3. Kad TUI bude gotov: BRISANJE Nicka (192.168.178.38, ssh korisnik@...,
   popis brisanja u e2e-nalazima, sekcija "DOGOVOR") pa čista E2E proba
   pod imenom ATLAS — ali SAMO ako izričito kažem "kreni s probom".

4. NE diraj: WinSW/NSSM i DPAPI (čekaju čistu probu), cloud LLM opcija
   (čeka moju odluku), chat lane redizajn + web UI fronta (zasebna faza).

5. Završni izvještaj: grane, suite, CI, što je ostalo.

---

Napomena za mene (Claude): memorija ažurirana — vidi ragspine-stanje-2026-08-06
(sada opisuje ATLAS stanje) i ragspine-sdd-proces.
