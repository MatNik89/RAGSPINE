# C3 — Hibridna ekstrakcija polja (regex + LLM) + rok-alert

Cilj: iz teksta dokumenta (OCR/ingest) automatski pročitati polja definirana u
`doc_types` registru (C2) i za polja označena `expiry` upisati istek u
`expiry_items` — dashboard ih već prikazuje (≤7 dana = warn, prošlo = bad).

## Tijek

1. `extract(spine, cfg, doc_id, doc_type_key, llm, client_id)`:
   tekst dokumenta = chunks JOIN po `doc_id` (ORDER BY seq).
2. **Regex prolaz** po svakom polju: traži labelu (ili key) uz vrijednost u
   istom retku; `date` polja hvataju hrvatski format (`15. 8. 2026.`,
   `15.08.2026`) i ISO — normalizira se u `YYYY-MM-DD`.
3. **LLM prolaz** samo za polja koja regex NIJE našao (jedan poziv, JSON out,
   `null` za nepoznato). LLM nedostupan/greška → degradacija na regex-only,
   nikad pad. Regex nalaz se NE prepisuje LLM-om.
4. Rezultat u `doc_extracts` (zadnja ekstrakcija po dokumentu, upsert).
5. `expiry` polje s valjanim datumom + poznat `client_id` → upsert u
   `expiry_items` (kind = doc_type_key, label = "Naziv vrste: Naziv polja");
   ponovna ekstrakcija ažurira postojeći red, ne duplicira.

## Model

`doc_extracts(doc_id PK, doc_type_key, client_id, fields_json, engines_json, at)`
— `engines_json`: {key: "regex"|"llm"} odakle je vrijednost.

## API

`POST /extract {doc_id, doc_type, client_id?}` → {fields, engines, expiry_created}.
`client_id` default iz `documents.client_id`.

## Rok-alert

Bez novog mehanizma: dashboard `expiring` panel (30 dana) + `_urgency`
(≤7 dana = warn) već pale alarm čim ekstrakcija upiše `expiry_items`.
