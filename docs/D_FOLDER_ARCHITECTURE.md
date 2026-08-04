# D — Prijedlog arhitekture mapa (preview + confirm)

Cilj: RAGSPINE predloži urednu strukturu mapa na NAS-u — **must-have mape
ureda** + **standardne podmape po klijentu** — pokaže što postoji a što fali
(preview), i tek na potvrdu kreira što nedostaje. Nikad ne briše i ne premješta
postojeće; samo dodaje prazne mape.

## Struktura (default, konstante)

Must-have (korijen = nas_root ili data_dir):

- `KLIJENTI` — po klijentu (onboarding već stvara `klijenti/{id}_{slug}`)
- `PROPISI` — zakoni/pravilnici (folder-sync tier iz podmapa)
- `SCANNER` — ulaz skeniranih dokumenata (piece E)
- `ARHIVA` — završeni/stari predmeti

Per-klijent podmape (u `clients.nas_folder` mapi):

`Osobni dokumenti`, `Ugovori`, `Izvodi`, `Računi`, `Porezna`

ponytail: lista je konstanta u kodu — upgrade path: editable template u
Postavkama (piece G polish).

## Tijek

1. `GET /folder-architecture` → propose(): za svaku mapu `{path, exists}` +
   po klijentu isto; ništa se ne dira na disku (read-only preview).
2. UI `/ui/arhitektura` (kartica u Postavkama): ✓/✗ pregled + dugme
   "Kreiraj mape koje nedostaju".
3. `POST /folder-architecture/apply` → apply(): `os.makedirs` SAMO za
   nedostajuće, svaki path guardan `security.path_under` (zli `nas_folder`
   s `..` se preskače, fail-closed); idempotentno (drugi apply = 0 novih);
   audit log.
