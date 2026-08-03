# A+B — Petlja spajanja + otkrivanje klijenata (dizajn)

**Datum:** 2026-08-03
**Kontekst:** RAGSPINE se koristi u JEDNOM knjigovodstvenom uredu (vlasnikov vlastiti
install). Ovo je prvi komad veće vizije (karta A–G). Cilj: kad vlasnik spoji shareanu
mapu, RAGSPINE ju pročita, javi što je našao, pita što dalje, zapamti napomene, i
otkrije klijente iz naziva podmapa. Sve mora raditi TEK NA SPAJANJU — gradi se i
testira protiv sintetičkih (namjerno zbrkanih) mapa jer prave nemam.

## Opseg (samo A+B)
- **A. Petlja spajanja i orijentacije** — spoji mapu → skeniraj → obavijest
  „spojeno, našao X, što dalje?" → napomene u memoriju.
- **B. Otkrivanje klijenata iz KLIJENTI** — nazivi podmapa → klijenti (bez OIB-a),
  pregled prije potvrde, veza dokumenata.
- **UI temelj (usporedno, lagano):** lijevi sidebar + dashboard kao početni ekran.
  Bez teškog vizuala — samo da su sve funkcije dostupne; izgled se dorađuje kasnije.

NE u ovom komadu: OCR/doc-inteligencija (C), reorg/premještanje (D), skener-tok (E),
wizard (F). Ovdje se samo POPISUJE i OTKRIVA; ništa se ne premješta ni ne mijenja na disku.

## UI temelj — lijevi sidebar + dashboard
- `page_shell` prelazi s gornje `nav.nav` na **lijevi sidebar** (`aside.sidebar` +
  `main`). Sidebar: brand, navigacijski linkovi (isti `_NAV` + novi po potrebi),
  badge neviđenih obavijesti, tema-toggle, odjava dolje. Responsive: na uskom ekranu
  sidebar se skupi (CSS, bez JS okvira).
- Dashboard (`/`) ostaje početni ekran; dobiva karticu **„Orijentacija"** (v. A) kad
  ima nedovršenih koraka spajanja.
- Postojeći ekrani rade nepromijenjeno (samo im se mijenja shell). Data-driven ostaje:
  fetch same-origin, `textContent`, `script_json` — nikad lažni podaci.

## A. Petlja spajanja i orijentacije
### Podatkovni model
- Postojeće: `folders(path, role, label, enabled, last_synced)`, `notifications`,
  `memory(user,key,value)`. Reuse.
- Nova uloga mape: `folders.ROLES` dobiva **`skener`** (uz `propisi|klijenti|ostalo`).
  KLIJENTI je `role='klijenti'`, SKENER `role='skener'` (skener-tok tek u komadu E;
  ovdje se samo registrira i popisuje).
- Nova tablica **`folder_scan(folder_id PK, at, n_subdirs, n_docs, n_pdf, n_pdf_no_text,
  summary_json)`** — zadnji rezultat popisa po mapi (za obavijest + „što dalje").

### Tok
1. Vlasnik u Postavke→Mrežne mape doda mapu i ulogu (postoji `/ui/mape`, `folders.register`).
2. Na dodavanje (i na gumb „Skeniraj sad") pokreće se **popisni skener** (read-only):
   prošeće stablo (scoped realpath, simlink-escape blokiran — postoji), izbroji
   podmape/dokumente/PDF-ove i **PDF-ove bez ekstrahiranog teksta** (heuristika:
   `pdfinfo`/pyMuPDF `page.get_text()` prazan → „nema OCR"; puna OCR obrada je komad C,
   ovdje samo BROJ). Rezultat → `folder_scan`.
3. Kreira se **obavijest** tipa `folder_connected`: „Spojena KLIJENTI: 42 klijentske
   mape, 1200 dokumenata, 300 PDF bez pretraživog teksta." + **predložene sljedeće akcije**
   (linkovi/gumbi, ovisno o ulozi):
   - klijenti → „Pregledaj otkrivene klijente" (v. B), „Skeniraj/OCR dokumente (kasnije)".
   - skener → „Skener-tok (kasnije)".
   - propisi/ostalo → „Sinkroniziraj u indeks" (postoji `folder_sync`).
4. **Napomene → memorija:** orijentacijska kartica ima polje „Napomena" (per mapa i
   globalno). Sprema se u `memory` (key `note:folder:{id}` ili `note:global`) i,
   kad krene chat, ulazi kao interni kontekst (postoji `pipeline._org_context`/memorija).
   Bez LLM-a — čisto perzistencija + kasnije injektiranje.

### Endpointi
- `POST /folders/{id}/scan` → pokreni popis, vrati `folder_scan` sažetak (auth: web).
- `GET /folders/{id}/scan` → zadnji sažetak.
- `POST /notes/folder` `{folder_id?, body}` → spremi napomenu u memoriju.
- Obavijesti idu kroz postojeće `/notifications.json` + dashboard karticu.

## B. Otkrivanje klijenata iz KLIJENTI
### Parsiranje naziva (bez OIB-a)
- Skeniraju se **neposredne podmape** KLIJENTI mape = klijent-kandidati.
- Naziv se NE over-parsira (mape su zbrkane): sprema se sirovi naziv. Heuristika tipa:
  sadrži `d.o.o.|j.d.o.o.|obrt|d.d.` → `company`, inače (dvije riječi, slova) → `person`
  (npr. „PERIĆ PERO"). Tip je samo oznaka za kasnije, ne mijenja ponašanje.
- OIB = NULL (nema ga u nazivu; dodaje se kasnije ručno ili kroz wizard/skener).

### Pregled prije potvrde (bitno — nazivi su zbrkani)
- `POST /clients/discover?folder_id=` → vrati **prijedlog** (bez upisa): lista
  `{subdir, raw_name, guessed_type, matches_existing_client_id?}`. Podudaranje s
  postojećima: normaliziran naziv (fold dijakritika, redoslijed riječi) → izbjegni
  duplikat/omogući spajanje.
- Ekran `/ui/klijenti-uvoz`: tablica kandidata; po retku akcije **Uvezi / Spoji s
  postojećim / Preskoči**; naziv se može urediti prije uvoza.
- `POST /clients/discover/commit` `{items:[{subdir, name, action, merge_id?}]}` →
  kreira/ažurira `clients` (name, `nas_folder`=relativni put podmape, oib=NULL),
  idempotentno; NE dira datoteke na disku.
- **Veza dokumenata:** dokumenti unutar klijentove podmape vežu se preko `nas_folder`
  prefiksa (retrieval/karton već čitaju po klijentu). Puni ingest/indeks tih dokumenata
  je komad C (OCR ovisan) — ovdje se samo uspostavi VEZA mapa↔klijent.

## Sigurnost / invarijante
- Sve read-only nad diskom u ovom komadu (popis + otkrivanje); nula premještanja/brisanja.
- Scoped realpath + simlink-escape (postoji u `folders._scoped`) vrijedi i za skener.
- Idempotentno: ponovni scan/discover ne duplicira klijente ni obavijesti
  (`_notify_once` postoji; discover po `nas_folder` upsert).
- Data-driven UI: `textContent`, `script_json`, `require_user_web`.

## Testiranje
- Sintetička KLIJENTI mapa (tmp): podmape „PERIĆ PERO", „PODUZEĆE X D.O.O.",
  ugniježđeni dokumenti, jedan PDF bez teksta → scan sažetak točan (brojevi),
  obavijest kreirana, tip pogođen, discover predlaže ispravno, commit idempotentan,
  veza mapa↔klijent postavljena, napomena perzistirana.
- Sidebar shell: svi postojeći ekrani se renderiraju (smoke), aktivni link označen.

## Kasnije (izvan A+B, za kontekst)
C doc-inteligencija (OCR audit/ekstrakcija polja/rokovi→dashboard), D reorg,
E skener, F wizard, G watchlist-UI/Excel/vizual-poliš.
