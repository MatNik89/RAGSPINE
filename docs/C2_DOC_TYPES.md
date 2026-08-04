# C2 — Registar vrsta dokumenata (doc_types)

Cilj: data-driven registar vrsta dokumenata (kao `obligation_types` za obveze).
Svaka vrsta nosi **polja za ekstrakciju** koja C3 (hibridna ekstrakcija
regex+LLM) puni iz skeniranih dokumenata; polje označeno `expiry` daje datum za
rok-alert (7 dana prije isteka, C3).

## Model

Tablica `doc_types`:

| kolona      | tip     | napomena                                   |
|-------------|---------|--------------------------------------------|
| key         | TEXT PK | snake_case šifra (npr. `osobna_iskaznica`) |
| label       | TEXT    | naziv za UI                                |
| fields_json | TEXT    | JSON lista polja                           |
| active      | INT     | 1 = nudi se u UI-ju                        |
| sort        | INT     | redoslijed                                 |

Polje: `{"key","label","kind","expiry"}`; `kind ∈ text|date`;
`expiry=true` samo na `date` polju (datum koji istječe).

Seed (INSERT OR IGNORE — admin-izmjene ostaju): **osobna_iskaznica** s poljima
broj (text), datum_izdavanja (date), mjesto_izdavanja (text),
datum_isteka (date, expiry).

## API

- `GET /doc-types` — registar (fields parsirani)
- `POST /doc-types` — upsert (key se normalizira u snake_case; validacija polja)
- `GET /doc-types/export` — JSON download (backup/dijeljenje između ureda)

Brisanja nema (kao kod vrsta obveza) — deaktivacija preko `active=0`.

## UI

Postavke → kartica "Vrste dokumenata" → `/ui/dok-tipovi`: tablica + forma s
dinamičkim retcima polja (key/label/kind/istek) + dugme "Izvoz JSON".

## Sljedeće (C3, ne ovdje)

Hibridna ekstrakcija (regex+LLM) po `fields_json` + rok-alert na dashboard.
