# D2 — Arhitektura mapa: dogovor, ne diktat

Revizija D-a po korisnikovoj korekciji: **ništa se ne izmišlja u kodu**.
Stvarni NAS ureda ima mapu `KLIJENTI` (velikim slovima) u kojoj su SVI
klijenti — ATLAS je skenira i **dogovara** strukturu s korisnikom, pa
dogovor **trajno sprema**.

## Izvori istine

1. **Klijenti = disk.** `klijenti_root()` = registrirana Mrežna mapa s ulogom
   `klijenti` (stvarna KLIJENTI); fallback `{root}/klijenti` samo ako postoji.
   Svaka direktna podmapa = klijent. Onboarding nove klijente stvara TAMO.
2. **Template = dogovor.** `config_overrides` modul `arhitektura`:
   `{"office": [...], "client_subdirs": [...]}`. **Default prazan** — dok se
   ne dogovorimo, ništa se ne predlaže.

## Tijek

1. `learn_structure()` — pročita POSTOJEĆE stanje: broj klijenata + frekvencija
   njihovih podmapa ("Ugovori kod 8 klijenata…"). Polazište razgovora.
2. **Dogovor** — u chatu (lane `arhitektura`):
   - "dogovor mape po klijentu: Ugovori, Izvodi, Porezna" → spremi
   - "dogovor uredske mape: SCANNER, ARHIVA" → spremi
   - bilo što drugo o strukturi mapa → pregled naučenog + dogovorenog
   ili u Postavke → Arhitektura mapa (isti template, editabilan).
3. `propose()` — preview ✓/✗ iz dogovora: uredske mape uz korijen + dogovorene
   podmape za svakog klijenta s diska.
4. `apply()` — na potvrdu (admin/owner) kreira SAMO nedostajuće; idempotentno;
   nikad ne briše/premješta; simlink-escape preskočen fail-closed; audit.

## API (sve admin/owner)

`GET /folder-architecture` (preview) · `GET /folder-architecture/learned` ·
`GET|POST /folder-architecture/template` · `POST /folder-architecture/apply`

Imena mapa validirana: bez `/ \\ .. : * ? < > |`, kontrolnih znakova i završne
točke (NTFS je strippa).
