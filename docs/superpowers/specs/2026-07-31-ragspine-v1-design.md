# RAGSPINE v1 — dizajn (CLAUDE verzija)

Datum: 2026-07-31
Status: odobreno (brainstorming s userom)

## Što je

AI asistent za hrvatske knjigovođe. Nije ERP — ne knjiži, ne generira račune, ne
šalje JOPPD. Chat je glavni interface; uz njega `/obveze` HTML stranica,
periodička watchlista službenih izvora i Chrome extension bridge.

Ovo je CLAUDE-ova vlastita verzija: spec drugog agenta služi kao popis
zahtjeva (feature set se poklapa 100%), implementacija i organizacija su naše.

## Odluke iz brainstorminga

1. **Scope**: SVE iz speca u v1 — puni feature set, ravnopravna usporedba s
   KILO/HERMES/CODEX verzijama.
2. **Ovisnosti**: core radi samo s FastAPI+uvicorn+stdlib. Sve teško
   (PyMuPDF, FastEmbed, sqlite-vec, LiteLLM, browser-use, python-docx,
   openpyxl, feedparser…) je OPTIONAL s urednom degradacijom. Doctor javlja
   što fali i što se time gubi.
3. **Struktura**: paketi po domeni, svaki modul jedan fokusiran fajl.

## Arhitektura

```
ragspine/
  __init__.py
  __main__.py     CLI: serve, setup, doctor, health, ingest, forget, eval,
                  stats, reminders, auth, browser
  config.py       sve env varijable na jednom mjestu (RAGSPINE_* prefiks)
  core/
    spine.py      SQLite WAL; per-thread read konekcije, globalni write_lock;
                  schema 35+ tablica u jednom fajlu; get_override/set_override
    llm.py        provider dispatcher: OpenAI-compat (DeepSeek/Kimi/Ollama) +
                  Anthropic Messages; auto-detekcija iz URL-a; API key ili
                  OAuth token fallback (čita Claude Code / Codex tokene s
                  diska); urllib, LiteLLM samo ako je instaliran
    security.py   JWT (hashlib+hmac, HS256), OIB checksum ISO 7064 mod-11,
                  PII detekcija+redakcija (mail, telefon, IBAN, OIB),
                  SHA-256 hash-chain audit (append-only + verify)
    subproc.py    izolirani subprocesi: timeout, kill-tree (process group),
                  resource limiti
    optional.py   jedan helper za optional import + registry što fali
  rag/
    embed.py      FastEmbed BAAI/bge-m3 wrapper (optional)
    retrieval.py  hibrid FTS5 + sqlite-vec + RRF; freshness filter za
                  verzionirane dokumente
    router.py     30+ regex pravila (HR jezik): sql, web, graf, ocr, learn,
                  knjizenje, no_retrieval, reject, chat
    composer.py   prompt s označenim izvorima [ZAKON]/[KONTNI]/[SOP]/[DOK]/[ERAČUN]
    citations.py  verifikacija da odgovor citira dohvaćene dokumente,
                  confidence score; bez citata → "ne znam", ne izmišlja
    cache.py      hash upita, dedup, 24h TTL
    sql_lane.py   template matching za brojčane upite (koliko računa, zbroj
                  PDV-a, top klijenti)
    selfrag.py    klasifikacija kompleksnosti → adaptivna strategija;
                  LLM relevance check; fallback DuckDuckGo web search
    graphrag.py   ekstrakcija entiteta (OIB, konto, klijent, zakon, datum,
                  iznos) → kg_edges → multi-hop traversal → merge s vektorskim
  docs/
    ingest.py     parser ladder (PDF PyMuPDF, DOCX, XLSX, TXT); chunker s
                  detekcijom tipa (račun/ugovor/bilanca/zakon/SOP); hash dedup;
                  bulk ingest foldera
    ocr.py        Unlimited-OCR klijent (SGLang/vLLM HTTP); PyMuPDF raster
                  300dpi PNG; nevidljivi text layer nazad u PDF; bulk sa
                  skip-if-text-layer
    eracun.py     UBL 2.1 XML parser (OIB kupca/dobavljača, stavke, PDV);
                  auto-sort: OIB → klijent u bazi → premjesti u NAS folder
    imap_fetch.py IMAP prilozi, UID watermark
    pdfforms.py   AcroForm filler iz baze
    forget.py     GDPR: sweep svih tablica, WAL TRUNCATE, verifikacija
  business/
    place.py      bruto→neto 2026 (20%/30%, prirez 27 gradova iz overrides pa
                  default, olakšice za djecu, invalidnost)
    dnevnice.py   34 države, puna/pola dnevnica, smještaj, prijevoz
    quickref.py   24 referentne brojke s izvorom, pretraživo
    obveze.py     mjesečne obveze po klijentu (PDV/JOPPD/DOH), pdv_status filter
    kalendar.py   12 tipova rokova, RRULE, seed za tekuću godinu
    expiry.py     istek dokumenata (osobne, putovnice, certifikati, potpisi)
    checklist.py  kompletnost klijenta, score 0-100%
    notes.py      kronološke bilješke, pretraživo
    auditlog.py   tko-što-kada, pretraživo
    dashboard.py  agregati (aktivni klijenti, rokovi, top upiti)
    monthly.py    mjesečni pregled = kalendar+watchlist+obveze+istek+bilješke
  knowledge/
    kb.py         Q&A parovi, difflib similarity >60% → spremljeni odgovor
    translate.py  LLM prijevod, 16 jezika
    features.py   feature requestovi s prioritetom
    patterns.py   grupiranje sličnih upita, 5+ ponavljanja → prijedlog skilla
  web/
    api.py        FastAPI app; routeri po kategorijama; /v1/chat/completions
                  (OpenAI-compat za Open WebUI); /obveze HTML; /health
    watchlist.py  DB-driven watch_sources; fetch+hash usporedba; promjena →
                  ingest + ekstrakcija brojki → config_overrides + notifikacije;
                  RSS NN (3 feeda) s keyword matchingom za 8 industrija;
                  law diff ("Članak 5: 10% → 12%"); prefetch stale zakona;
                  upcoming_changes (datumi stupanja na snagu)
    learn.py      "nauči s URL-a": urllib fetch + html.parser čišćenje;
                  grad+stopa prireza regex; upis u config_overrides + ingest
  ops/
    doctor.py     preflight: Python, disk, RAM, NTP, LUKS, Ollama, OCR server,
                  optional deps status
    health.py     disk, WAL size, integrity_check; Apprise notifikacije (opt)
    nis2.py       NIS2 checklist 12 kontrola; SMART/Lynis/nmap stubovi
    setup.py      wizard: kreiraj bazu, seedovi (kontni plan, kalendar,
                  quickref, watch izvori), llmfit subprocess, hw detekcija
                  (CPU/RAM/GPU/Apple Silicon), Ollama+OCR provjera, LLM
                  provider detekcija (env keyevi + OAuth tokeni na disku)
  browser/
    bridge.py     in-memory command queue; /browser/cmd + /browser/result
    agent.py      CDP kontrola kroz browser-use (optional)
    workflows.py  Chrome DevTools Recorder JSON import, {{placeholder}}
    sessions.py   auto/keep/immediate modovi; 2 greške → promjena moda
extension/
  manifest.json   MV3
  background.js   polling /browser/cmd, izvršavanje (klik, upis, skrol,
                  screenshot), POST /browser/result
  popup.html/js   status + config (server URL, token)
tests/            pytest, zrcali strukturu paketa
```

## Tok podataka

1. Chat: upit → router → lane:
   - `chat`: retrieval (FTS5 + vec + RRF) → composer → LLM → citations gate →
     cache → odgovor s citatima. KB provjera prije LLM-a (difflib >60%).
   - `sql`: template match → SQL nad bazom → formatiran odgovor.
   - `learn`: URL fetch → ekstrakcija → config_overrides + ingest.
   - `ocr`: OCR zadatak u pozadini.
   - `web`: DuckDuckGo pretraga (i Self-RAG fallback).
   - `reject`/`no_retrieval`: direktno.
2. Watchlist neovisan o chatu: periodički (CLI/cron/API trigger) fetch svih
   aktivnih watch_sources → hash diff → ingest + brojke → config_overrides →
   notifikacije + law diff + upcoming_changes.
3. **config_overrides = centralni mehanizam**: tablica (module, key, value,
   source_url, updated_at). Svaki modul čita override prije hardcoded defaulta.
   Pune ga watchlist i learn lane.

## Baza

Jedan SQLite fajl, WAL mod. Per-thread read konekcije (threading.local),
jedan globalni write_lock za pisanje. Raw SQL, bez ORM-a. Tablice (35+):
documents, chunks, chunks_fts (FTS5), vec_chunks (sqlite-vec, opt), clients,
users, watch_sources, watch_state, law_versions, upcoming_changes, obligations,
obligation_status, notes, config_overrides, audit_log, hash_chain, knowledge,
reminders, feedback, memory, kontni_plan, cjenik, kg_nodes, kg_edges,
interactions, query_cache, expiry_items, feature_requests, skill_suggestions,
imap_state, browser_workflows, browser_sessions, deadlines, quickref,
dnevnice_rates, notifications, eracuni.

## Degradacija (optional deps)

`core/optional.py`: `need(name)` vraća modul ili None + registrira nedostatak.

| Fali | Gubi se | Ostaje |
|---|---|---|
| PyMuPDF | PDF ingest, OCR raster, text layer | DOCX/XLSX/TXT ingest |
| FastEmbed / sqlite-vec | vektorska pretraga | FTS5-only (RRF degradira na FTS rank) |
| LiteLLM | proxy routing | vlastiti urllib dispatcher (uvijek radi) |
| browser-use | CDP agent | extension bridge |
| feedparser | RSS parsing | vlastiti minimalni RSS parser (stdlib XML) |
| python-docx / openpyxl | DOCX/XLSX ingest | ostali formati |
| Apprise | push notifikacije | notifikacije u bazu + log |

## Sigurnost

- JWT (hashlib+hmac HS256, stdlib), users tablica, `ragspine auth add`.
  Svi endpointi traže token osim /health i /docs login stranice.
- SSRF guard na SVIM vanjskim fetchevima (watchlist, learn, web search):
  samo http/https, DNS resolve → block privatnih/loopback/link-local IP-ova,
  osim eksplicitno allowlistanih hostova u configu.
- Path sanitizacija na NAS operacijama (eracun auto-sort, bulk ingest):
  resolve + provjera da je unutar konfiguriranog roota.
- PII redakcija prije slanja LLM-u kad je konfigurirano (redact_pii flag).
- Pydantic validacija na svim API inputima.
- Audit log + hash-chain za mutacije.
- Browser bridge: token-autenticiran, extension polling s istim JWT.

## Testiranje

- TDD po modulu (test prvo, pa implementacija).
- Čisti Python (kalkulatori, router, citations, eracun, OIB, RRF): puni unit.
- DB moduli: tmp SQLite fixture.
- LLM/OCR/web: fake transport (monkeypatch urllib / fake server response).
- Runnable checkovi: `pytest` + `python -m ragspine doctor`.
- `ragspine eval`: mali golden set upita (router lane + retrieval hit).

## Redoslijed gradnje

1. config + core/spine (baza, schema, overrides) + core/optional
2. core/security + core/subproc
3. web/api skeleton (health, auth) + CLI skeleton
4. docs/ingest + rag/embed + rag/retrieval (pretraga radi)
5. rag/router + composer + core/llm + citations + cache (chat radi)
6. rag/sql_lane + selfrag + graphrag
7. web/watchlist + learn
8. business/* (kalkulatori pa poslovni moduli)
9. docs/ocr + eracun + imap + pdfforms + forget
10. knowledge/* + ops/*
11. browser/* + extension/
12. setup wizard + eval + finalni doctor

## Izvan scopea

Knjiženje, generiranje računa, JOPPD slanje (RAGSPINE nije ERP). Docker
(opcionalan, nije v1). Open WebUI se NE builda — samo /v1/chat/completions
kompatibilnost + uputa za spajanje.
