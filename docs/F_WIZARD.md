# F — DODAJ NOVOG KLIJENTA wizard + AI sidebar

Ekran `/ui/novi-klijent` (link s Klijenata): tri koraka + AI sidebar koji
**gleda tipkanje** (debounce 600 ms) i uz rub provjerava podatke.

## Koraci

1. **Osnovno** — naziv, OIB (ključan: e-računi, autosortiranje, buduće
   povezivanje satnice/PLAĆE), **pravni oblik: obrt / poduzeće**, kontakt.
   Poduzeće forsira obračun "dobit" (dohodak/paušal se odbija i na backendu).
2. **Porezni status** — obračun (dobit/dohodak/paušal), paušal EUR/mj,
   PDV status + frekvencija, zaposleni.
3. **Dokumenti koji se prate** — checkbox lista iz C2 registra vrsta
   dokumenata (`client_doc_types` veza): što se za klijenta skenira,
   automatski čita (C3) i prati istek.

## AI sidebar (`POST /clients/assist`)

Hibrid, kao C3 — radi i bez LLM-a:

- **Pravila** (deterministička): OIB kontrolna znamenka; poduzeće+paušal
  nekonzistentno; brojke (PDV prag, paušalni razredi) iz **quickref registra**
  s overrideima — nikad hardkod u pravilu.
- **Obrasci institucija** za situaciju (obrt/poduzeće/zaposleni/PDV):
  kratka lista koraka (Obrtni registar, RPO, HZMO e-prijava, JOPPD, P-PDV…).
- **RAG citati** iz propisa (retrieval po aspektima drafta, max 4 izvora).
- **LLM sažetak** (opcionalno): 2-3 rečenice, grundan isključivo na danim
  izvorima; LLM pad → sidebar radi dalje bez sažetka.

## Model

`clients.legal_form` ('', 'obrt', 'poduzece') — _ensure_columns migracija.
`client_doc_types(client_id, doc_type_key)` — praćene vrste po klijentu
(GET `/clients/{id}/doc-types`; upis kroz `POST /clients` polje `doc_types`).

Kreiranje ide postojećim tijekom: mapa u KLIJENTI (D2), obavijest, karton.
