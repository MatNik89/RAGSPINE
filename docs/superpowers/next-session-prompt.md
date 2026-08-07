# Prompt za sljedeću sesiju (pripremljen 2026-08-07)

Kopiraj-zalijepi u novu sesiju:

---

Nastavi RAGSPINE rad. Master @ 3765b5c, sve mergeano i pushano, suite lokalno
1147 passed / 1 skipped. Jučer: deferred minori + upgrade grana wizarda +
prva E2E proba na stvarnom Windowsu (stroj "Nick") — svih 6 stranica wizarda
prošlo, usput popravljeno 6 bugova (BOM, EAP=Stop, mrtvi Rokovi link, chat
cookie 401, websearch mrtvi kraj, PDV rok 2026 + migracija). SVI nalazi i
specifikacije dorada: docs/e2e-nalazi-2026-08-06.md — pročitaj cijeli prije
početka.

Radiš autonomno u loopu do kraja; javi se tek kad SVE završi. Proces: SDD
(superpowers:subagent-driven-development) na feature grani; merge = lokalni
merge u master + push origin master (moj ustaljeni izbor, ne pitaj); modeli:
haiku transkripcija / sonnet integracija+review / fable finalni review;
hrvatski latinica s dijakriticima — NIKAD ćirilična slova, NIGDJE (ni u chat
odgovorima); bez novih depsa osim dogovorenih ispod; testovi bez mreže/
stdina/pravih subprocessa; puni suite U PRVOM PLANU (i subagentima to izričito
reci); hrvatske konvencionalne commit poruke s Co-Authored-By: Claude Fable 5
footerom.

Zadaci redom:

0. CI provjera: GitHub Actions bio u Major Outageu 6.-7.8. — runovi za
   commite 89d856a..3765b5c možda uopće nisu kreirani, stari run 31124742918
   (49577f6) visi queued s korumpiranim stanjem (cancel i force-cancel
   odbijeni). Provjeri `gh run list --branch master`; ako novi runovi
   postoje — čitaj po starom pravilu (infra fail = rerun, test fail = SDD
   fix); ako ne postoje, pushevi iz ove sesije će ih stvoriti.

1. RENAME RAGSPINE → ATLAS (jedna grana, SDD). Prvo kratki brainstorm opsega
   pa writing-plans pa implementacija. Minimalno u opsegu: Python paket
   ragspine/ → atlas/, CLI naredba, env varijable RAGSPINE_* → ATLAS_* (SA
   ALIASIMA za stare — postojeće instalacije ne smiju puknuti), data dir
   ~/.ragspine → ~/.atlas (s migracijom/fallbackom na postojeći), poruke i
   naslovi (wizard, web, README, install skripte), cookie ime, User-Agent/
   Server header, schtasks/servis imena u dokumentaciji. Odluke koje doneseš
   sam: redoslijed sigurnih koraka; što ostaje kompatibilno zauvijek vs. do
   v2. GitHub repo ime NE diraj bez mene (remote URL se mijenja — to radim
   ja ručno na GitHubu, ti pripremi upute).

2. TUI face-lift wizarda (druga grana, SDD) — cijeli spec je u
   docs/e2e-nalazi-2026-08-06.md: hermes curses mehanika (strelice, razmak,
   Enter, ESC natrag; numerirani fallback bez cursesa), windows-curses kao
   JEDINA dopuštena nova ovisnost (Windows only), tablica modela s
   rangiranim namjenama + Disk stupcem, folder picker za mape, živi progress
   (winget + ollama pull \r), PATH refresh bez restarta terminala, Tesseract
   auto-install s hrv paketom i poznatom lokacijom, getpass za lozinku,
   install.ps1 uskladba, prečac na desktopu (sve platforme), cert bootstrap
   stranica + prijateljsko ime (fritz.box/mDNS) u SAN-u i uputama.
   Ako je prevelik zalogaj za jednu sesiju: prioritet je curses jezgra +
   stranice 1 i 3; ostalo dokumentiraj kao sljedeću granu.

3. NE diraj: WinSW/NSSM i DPAPI (čekaju čistu Windows probu), cloud LLM
   opcija (čeka moju odluku), chat lane redizajn (zasebna faza s web UI).

4. Nick (Windows test stroj, 192.168.178.38, ssh korisnik@..., ključ radi):
   server tamo vrti stari kod kroz Scheduled Task "RAGSPINE-serve" — NE
   deployaj rename tamo; nakon TUI-ja slijedi BRISANJE Nicka i čista proba
   (popis brisanja u nalazima). Nick koristi samo ako trebaš provjeriti
   nešto Windows-specifično čitanjem.

5. Završni izvještaj: što je napravljeno po granama, suite, CI stanje,
   što je ostalo.

---

Napomena za mene (Claude): memorija ažurirana u
~/.claude/projects/.../memory/ — vidi ragspine-stanje-2026-08-06 i
ragspine-sdd-proces.
