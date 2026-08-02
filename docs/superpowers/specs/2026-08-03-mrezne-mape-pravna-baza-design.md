# Mrežne mape + pravna baza + iterativni provjereni odgovor — dizajn

**Datum:** 2026-08-03
**Status:** odobren dizajn (čeka pregled speca prije plana)

## Cilj

RAGSPINE se spaja na mrežne mape na drugim uređajima u LAN-u (Windows server + Synology NAS, oboje preko SMB-a). Jednu mapu čine hrvatski propisi (zakoni/pravilnici/uredbe). Na upit RAGSPINE prolista propise, da **točan odgovor s citatima**, kaže **postotak točnosti**, i **nije yes-man**: prije svakog odgovora provede **više iteracija provjere**, a ispod **80%** ne nagađa nego odbije uz obrazloženje.

## Nefunkcionalne odluke (iz brainstorminga)

- **SMB za sve** (Windows share i Synology govore SMB/CIFS) — jedan mehanizam.
- **OS montira, RAGSPINE čita.** Korisnik postavlja mount (uz AI pomoć); lozinke share-a idu u OS credentials datoteku (0600), **nikad u aplikaciju**. RAGSPINE čita read-only.
- **Prag odbijanja 80%** uz obrazloženje.
- **Iterativni odgovor**: više prolaza retrieve→provjeri→dopuni prije odgovora, ograničeno (max N prolaza).
- Jezgra (hibridni retrieval, citati, IDK-gate, authority blend, sha-dedup ingest, doc versioning) **već postoji** — ovo je nadogradnja, ne od nule.

## Arhitektura — tri dijela

### Dio A — Registar mapa (RAGSPINE vidi NAS, korisnik dodjeljuje uloge)

**Podatkovni model**
- Nova tablica `folders(id, path, role, label, enabled, added_by, added_at)`.
  - `path` — apsolutna putanja mount-mape (npr. `/mnt/nas/Zakoni`).
  - `role` — `zakoni` | `klijenti` | slobodni string (`ostalo`/custom, dodaje se kad zatreba, kao vrste obveza).
  - `enabled` — uključena u sinkronizaciju.
- Config: `mount_roots` (lista dozvoljenih korijenskih putanja, npr. `/mnt/nas,/mnt/windows`). **Samo** mape ispod ovih korijena smiju se registrirati (sprječava čitanje proizvoljnog FS-a).

**Endpointi** (svi `require_user_web`)
- `GET /folders/browse?path=<p>` — izlista **samo neposredne podmape** putanje `p`. Scoping: `realpath(p)` mora biti ispod nekog `mount_root` (isti obrazac realpath+commonpath kao onboarding/eracun). Vraća imena mapa; ne izlazi iz korijena; ne prati simlinkove van korijena.
- `GET /folders` — registrirane mape.
- `POST /folders` — registriraj `{path, role, label}`; validira: path ispod mount_root, postoji, je direktorij.
- `POST /folders/{id}` — uredi (role/label/enabled).
- `DELETE /folders/{id}` — makni iz registra (ne briše s diska).

**UI** — ekran `/ui/mape`:
- Lijevo: browser montiranih korijena (klik ulazi u podmapu, koristi `/folders/browse`).
- Desno: dodijeli ulogu odabranoj mapi (`zakoni`/`klijenti`/custom) + spremi.
- Tablica registriranih mapa (putanja, uloga, uključena, zadnja sinkronizacija).
- Dostupno svim radnicima (kao registar vrsta obveza).

**Sigurnost**: sve putanje kroz `realpath` + `commonpath` provjeru ispod `mount_roots`; read-only; bez SMB kredencijala u aplikaciji; auth na svim rutama.

### Dio B — Sinkronizacija i ingest po ulozi

- **Job `folders_sync`** (raspored: satno ili dnevno): za svaku `enabled` mapu prohoda datoteke, ingesta nove/promijenjene.
  - Promjena datoteke = sha se razlikuje → nova verzija dokumenta (`documents.version/supersedes/status` već postoji), stara → `superseded`.
  - sha-dedup već postoji u `ingest` (bez duplog rada).
- **Uloga `zakoni`**: doc_type + **autoritet iz putanje/naziva**:
  - segment/naziv sadrži `zakon` → tier 1.0; `pravilnik` → 0.95; `uredb` → 0.9; `mišljenj`/`misljenj` → 0.85; `NN`/`narodne novine` → 0.8; `koment`/interno → 0.7 (mapiranje na postojeći `authority.py`).
  - Autoritet se sprema uz dokument/chunk pa retrieval `blend_authority` diže povjerenje za jače izvore.
- **Uloga `klijenti`**: generalizira sadašnji `nas_root` — mapa u kojoj su podmape klijenata (veže se na postojeći onboarding). Više `klijenti` mapa dozvoljeno.
- **Uloga custom/`ostalo`**: ingesta se kao opći dokumenti (doc_type='dok').
- Degradacija: nedostupna mapa (mount pao) → job preskoči + zabilježi upozorenje, ne ruši se.

### Dio C — Iterativni provjereni odgovor (ne yes-man, 80% prag)

Nadogradnja postojećeg `selfrag`/`pipeline` u **višeprolazni provjereni odgovor** za pravne upite:

1. **Retrieve** (hibrid FTS5+vektori+RRF) za upit.
2. **Nacrt** utemeljenog odgovora s citatima (composer + LLM).
3. **Provjera (verify pass)**: svaka tvrdnja mora imati pokrivajući citat; detektiraj rupe i proturječja među izvorima; ako ima rupa → **preformuliraj upit i ponovno retrieve** (do **N=3** prolaza).
4. **Izračun točnosti**: `coverage × validity × authority_blend` (postojeće komponente), 0–100%.
5. **Odluka**:
   - `≥ 80%` → odgovor + **"Točnost: N%"** + citati + kratko **obrazloženje** (na čemu se temelji, koliko izvora).
   - `< 80%` → **ne odgovara**: "Nisam dovoljno siguran (N%) — [obrazloženje: premalo/preslab izvor / proturječni izvori]." + (opcionalno) što je našao da korisnik dovrši.
6. **Ne yes-man** (prompt): ne prihvaćaj neutemeljene premise; ako izvori proturječe premisi pitanja, **eksplicitno ospori** uz citat; nikad ne izmišljaj; bez pokrivajućeg izvora → "Ne znam" (postojeći IDK-gate).
- **Ograničenje**: max N prolaza (bez beskonačne petlje); svaki prolaz jeftin.
- **Degradacija bez LLM-a**: fallback na jedan prolaz + prag na citation-confidence (postojeći put), s jasnom oznakom da je bez LLM-provjere.

## Komponente (datoteke)

- `ragspine/business/folders.py` — registar (CRUD, scoping, browse), uloge.
- `ragspine/config.py` — `mount_roots`.
- `ragspine/core/spine.py` — tablica `folders`.
- `ragspine/ops/jobs.py` — `folders_sync` job.
- `ragspine/docs/ingest.py` — ingest s autoritetom-iz-putanje (mala nadogradnja).
- `ragspine/rag/authority.py` — mapiranje putanja→tier (ako već nema).
- `ragspine/rag/verify.py` (novo) — višeprolazna provjera + odluka o pragu.
- `ragspine/rag/pipeline.py` — ukey verify-loop za pravne upite + prikaz točnosti/odbijanja.
- `ragspine/web/api.py` + `templates_*` — `/folders*` endpointi + ekran `/ui/mape`; prikaz "Točnost: N%" u chatu.

## Sigurnost (invarijante)

- Sve mrežne putanje: `realpath` + `commonpath` ispod `mount_roots`; nema izlaska iz korijena; simlink-escape blokiran; **read-only** (nikad pisati u `zakoni`).
- SMB kredencijali samo u OS-u (0600), nikad u aplikaciji ni u chatu.
- Svi novi endpointi `require_user_web`.
- Ingest: postojeći parser-ladder + veličinski limiti; XXE/HTML-safe kako već jest.
- Prikaz: `textContent` za podatke iz API-ja; bez vanjskih resursa.

## Testiranje

- Registar: CRUD; **odbijanje putanje izvan `mount_root`** (400); browse ne izlazi iz korijena; simlink-escape odbijen.
- Sync: ingesta nove datoteke; promijenjena → nova verzija, stara superseded; **autoritet iz putanje** (Zakoni/ → 1.0, Pravilnici/ → 0.95); nedostupna mapa → preskok bez pada.
- Verify-loop: ispod 80% → odbija uz obrazloženje; iznad → odgovara s "Točnost: N%" + citati; neutemeljena premisa nije prihvaćena; **ograničen broj prolaza**; bez LLM-a → fallback put.
- Sigurnost: browse/registar bez auth → 401/redirect; path-traversal odbijen.

## Faze (svaka samostalno testabilna)

- **Faza 1 — Registar mapa + Mape UI**: `folders` tablica, `mount_roots`, `/folders*` + scoping, ekran `/ui/mape`.
- **Faza 2 — Sync + ingest po ulozi**: `folders_sync` job, autoritet-iz-putanje, verzioniranje, freshness.
- **Faza 3 — Iterativni provjereni odgovor**: `verify.py` višeprolazna petlja, 80% prag + obrazloženje, prikaz točnosti, anti-yes-man prompt.

## Izvan opsega (kasnije)

- RAGSPINE koji sam montira SMB (creds-management) — namjerno ne; OS montira.
- Full-text OCR skeniranih propisa (postojeći `ocr.py` pokriva po potrebi).
- Fino podešavanje broja prolaza/pragova po vrsti upita.
