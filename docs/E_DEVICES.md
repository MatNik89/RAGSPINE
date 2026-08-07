# E — Uređaji: skeneri + pisači (discovery, registar, odabir pri akciji)

Revidirano po korisniku: skeniraju se **svakakvi** dokumenti (tip dokumenta se
podešava u DODAJ KLIJENTA, piece F). U mreži je **više skenera i pisača** —
ATLAS ih pronađe (discovery), u Postavkama se svi dodaju, a kod svakog
skeniranja/printanja korisnik **bira uređaj**.

## Sigurnosni model

Obrnuto od web egressa: `safe_fetch` smije SAMO van (blokira privatne adrese),
uređaji smiju SAMO unutra — `lan_fetch` dopušta isključivo privatne/loopback
adrese, bez redirecta, s limitom veličine. Javna adresa u URL-u uređaja se
odbija i pri registraciji i pri svakoj upotrebi (fail-closed).

## Protokoli (bez novih ovisnosti, čisti stdlib HTTP)

- **Discovery: mDNS/DNS-SD** (UDP multicast 5353) — PTR upiti za
  `_uscan._tcp` / `_uscans._tcp` (eSCL skeneri) i `_ipp._tcp` (pisači);
  parse PTR→SRV→A/TXT. Best-effort (kratki timeout), nikad ne baca.
- **Skeniranje: eSCL (AirScan)** — POST ScanSettings XML na
  `{url}/ScanJobs`, poll `GET {job}/NextDocument` dok ne vrati 404;
  stranice (JPEG/PDF) se sklope u **jedan PDF** (fitz). Rezultat ide u
  registriranu mapu role='skener' (SCANNER), fallback `{data_dir}/scans`.
  Dalje ga preuzima postojeći tijek (OCR / folder-sync).
- **Printanje: IPP** — minimalni IPP 1.1 Print-Job (binarni request,
  ~80 linija) na vrata 631; dokument = PDF iz arhive (doc_id) ili scoped path.

## Model

`devices(id, kind scanner|printer, name, url, added_by, added_at)`.
Registar mijenja admin/owner; skeniranje/print koristi svaki radnik.

## API

- `GET /devices` — registrirani (svi ulogirani; za odabir pri akciji)
- `GET /devices/discover` — mDNS sweep (admin)
- `POST /devices` / `DELETE /devices/{id}` — admin
- `POST /scan {device_id}` → {path, pages} — sken u SCANNER mapu
- `POST /print {device_id, doc_id}` — pošalji dokument na pisač

## UI

- Postavke → **Uređaji**: "Pronađi uređaje" (lista nađenih + Dodaj),
  registrirani popis s brisanjem.
- **Dokumenti**: dugme "Skeniraj" s odabirom skenera; "Isprintaj" na
  dokumentu s odabirom pisača.
