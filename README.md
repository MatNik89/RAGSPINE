# ATLAS

*(ranije RAGSPINE)* <!-- compat: staro ime -->

AI asistent za hrvatske knjigovođe: chat s citiranim izvorima (RAG nad
propisima/dokumentima klijenata), `/obveze` pregled poreznih obveza,
periodička watchlista NN-a i drugih izvora, kalkulatori (plaća, dnevnice,
referentne brojke), OCR za skenirane dokumente i Chrome extension bridge za
poluautomatizaciju webova bez API-ja. **ATLAS nije ERP** — ne knjiži, ne
generira račune i ne šalje JOPPD.

## Početni setup (jedan blok po OS-u)

Python 3.11+. Kloniraj repo pa pokreni skriptu za svoj OS — napravi venv,
instalira sve i povuče embedding model. Postavljanje (operater, model,
HTTPS, mape) dovršava čarobnjak: `atlas setup`.
Idempotentno (ponovno pokretanje ne razbija install).

**Windows** (PowerShell, iz korijena repoa):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**Linux / macOS**:

```bash
./install.sh
```

Preskoči embedding model (RAG radi degradirano): `ATLAS_SKIP_MODEL=1`.

Nakon setupa:

```bash
atlas serve      # → http://127.0.0.1:8400/login
atlas doctor     # provjera spremnosti (korisnici, LLM, NAS, dozvole…)
```

Postavljanje u uredu (KLIJENTI mapa, uređaji, HTTPS):
**[docs/DEPLOY_URED.md](docs/DEPLOY_URED.md)**.

### Ručna instalacija (bez skripte)

Brže: `uv venv .venv --python 3.12` + `uv pip install -e ".[full]"` umjesto
`python -m venv` + `pip install` ispod (isti paketi, samo bez uv-a).

```bash
python -m venv .venv && . .venv/bin/activate     # Win: .venv\Scripts\Activate.ps1
pip install -e ".[full]"                          # ".": samo jezgra (degradirano)
atlas setup                                    # baza + sjemenke + detekcija
atlas auth add ana                             # prvi korisnik (owner)
atlas serve
```

`[full]` dodaje `pymupdf`, `fastembed`, `sqlite-vec`, `python-docx`,
`openpyxl`, `apprise`. Token programatski:

```bash
curl -X POST http://127.0.0.1:8400/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"ana","password":"lozinka"}'
```

## Konfiguracija

Sve preko `ATLAS_*` env varijabli (`atlas/config.py`):

| Varijabla | Opis |
|---|---|
| `ATLAS_DATA_DIR` | korijenski direktorij za bazu i secret (default `~/.atlas`) |
| `ATLAS_DB_PATH` | putanja SQLite baze (default `<DATA_DIR>/atlas.db`) |
| `ATLAS_HOST` | bind adresa servera (default `127.0.0.1`) |
| `ATLAS_PORT` | port servera (default `8400`) |
| `ATLAS_LLM_BASE_URL` | OpenAI-kompatibilni LLM endpoint (DeepSeek/Kimi/Ollama/LiteLLM proxy) |
| `ATLAS_LLM_API_KEY` | API ključ za `LLM_BASE_URL` |
| `ATLAS_LLM_MODEL` | naziv modela koji se šalje provideru |
| `ATLAS_ANTHROPIC_BASE_URL` | Anthropic Messages endpoint (default `https://api.anthropic.com`) |
| `ATLAS_OLLAMA_URL` | lokalni Ollama za detekciju/health (default `http://127.0.0.1:11434`) |
| `ATLAS_OCR_URL` | Unlimited-OCR (SGLang/vLLM) HTTP endpoint |
| `ATLAS_EMBED_MODEL` | FastEmbed model za vektorsku pretragu (default `intfloat/multilingual-e5-large`) |
| `ATLAS_NAS_ROOT` | korijen za auto-sort e-računa i bulk ingest (path-sanitiziran) |
| `ATLAS_IMAP_HOST` / `_IMAP_USER` / `_IMAP_PASS` | IMAP izvor za `ingest --imap` |
| `ATLAS_JWT_SECRET` | HS256 tajna (auto-generira se i sprema u `<DATA_DIR>/secret` ako nije postavljena) |
| `ATLAS_REDACT_PII` | `1` = redaktiraj PII (mail/telefon/IBAN/OIB) prije slanja LLM-u |
| `ATLAS_EGRESS_ALLOW` | zarezom odvojen popis hostova izuzet od SSRF blokade privatnih IP-ova |

## LLM provideri

`core/llm.py` je jedan dispatcher, bez obaveznih ovisnosti:

- **OpenAI-compat** (`LLM_BASE_URL` + `LLM_API_KEY`) — radi s DeepSeek, Kimi,
  Ollama, ili bilo kojim **LiteLLM proxyjem** (samo pokaži `LLM_BASE_URL` na
  proxy).
- **Anthropic Messages API** (`ANTHROPIC_BASE_URL` + `LLM_API_KEY`), auto-
  detektirano po obliku URL-a/ključa.
- **OAuth token fallback** — ako ključ nije postavljen, čita se postojeći
  Claude Code / Codex OAuth token s diska (isti mehanizam kao `setup`
  detekcija).

Sve preko stdlib `urllib` — LiteLLM se nikad ne importira direktno.

## Spajanje Open WebUI

ATLAS izlaže `/v1/chat/completions` i `/v1/models` u OpenAI-kompatibilnom
obliku. U Open WebUI dodaj novi OpenAI-connection:

- **Base URL:** `http://<host>:8400/v1`
- **API key:** JWT token dobiven s `/auth/login` (šalje se kao `Bearer`)

## Ključne značajke

- **Chat pipeline**: router (30+ HR regex pravila) → lane (`chat`/`sql`/
  `web`/`learn`/`ocr`/`reject`) → hibridna pretraga (FTS5 + sqlite-vec + RRF)
  → composer s označenim izvorima → LLM → citations gate (bez citata → "ne
  znam", nikad izmišljanje) → cache.
- **`/obveze`** — HTML pregled mjesečnih poreznih obveza po klijentu
  (PDV/JOPPD/DOH), s markiranjem poslano/nije.
- **Watchlist** — periodički fetch NN RSS feedova i drugih izvora, diff
  zakona ("Članak 5: 10% → 12%") upisuje se u `config_overrides` i budi
  notifikacije.
- **Learn s URL-a** — `nauči s <url>`: fetch + čišćenje + ekstrakcija brojki
  (npr. prirez grada) → `config_overrides` + ingest.
- **Kalkulatori** — plaća 2026 (bruto→neto, prirez 27 gradova, olakšice),
  dnevnice (34 države), quickref (24 referentne brojke s izvorom).
- **Porezni kalendar** — 12 tipova rokova, RRULE, seed za tekuću godinu.
- **E-račun** — UBL 2.1 parser + auto-sort po OIB-u u NAS folder klijenta.
- **OCR** — Unlimited-OCR klijent, PyMuPDF raster + nevidljivi text layer
  natrag u PDF (skenirani dokument ostaje pretraživ, izgled se ne mijenja).
- **Bez brisanja klijentskih podataka** — namjerno; knjigovodstvena dokumentacija ima zakonsku retenciju (podaci se ne brišu na zahtjev).
- **Browser extension** — MV3 bridge (polling command queue) za akcije na
  webovima bez javnog API-ja, umjesto punog CDP agenta.

## Degradacija (opcionalne ovisnosti)

| Fali | Gubi se | Ostaje |
|---|---|---|
| PyMuPDF | PDF ingest, OCR raster, text layer | DOCX/XLSX/TXT ingest |
| FastEmbed / sqlite-vec | vektorska pretraga | FTS5-only (RRF degradira na FTS rank) |
| LiteLLM | proxy routing | vlastiti urllib dispatcher (uvijek radi) |
| browser-use | CDP agent | extension bridge |
| feedparser | RSS parsing | vlastiti minimalni RSS parser (stdlib XML) |
| python-docx / openpyxl | DOCX/XLSX ingest | ostali formati |
| Apprise | push notifikacije | notifikacije u bazu + log |

`python -m atlas doctor` javlja točno što fali.

## CLI komande

| Komanda | Opis |
|---|---|
| `serve` | pokreni FastAPI server |
| `setup` | inicijalizacija baze, sjemenke, hw/provider detekcija |
| `doctor` | preflight (Python, disk, RAM, NTP, LUKS, Ollama, OCR, opcionalne ovisnosti) |
| `health` | brzi status baze i servisa |
| `ingest [path] [--imap]` | uvoz dokumenata iz foldera ili IMAP priloga |
| `eval` | pokreni golden-set upita, provjera router lane + retrieval hit |
| `stats` | interakcije po laneu, cache, top upiti |
| `reminders [add <text> <due>]` | podsjetnici |
| `auth add <user>` | novi korisnik (lozinka iz `ATLAS_PASS` ili prompt) |
| `browser status` | pending komande u browser bridgeu |
| `watch run` | ručno pokretanje watchliste |
| `ocr <path>` | OCR nad pojedinim dokumentom |

## Sigurnost

Postavljanje u uredu: **[docs/DEPLOY_URED.md](docs/DEPLOY_URED.md)**.

- JWT auth (HS256, stdlib `hashlib`/`hmac`) na svim endpointima osim
  `/health` i `/login`. Uloga se čita svježa iz `memberships` (revokacija odmah).
- **Vidljivost klijenata po radniku** enforcana kroz sve client-scope endpointe
  (dokumenti, bilješke, poruke, cjenik, ekstrakcija, obavijesti, isteci) i kroz
  RAG dohvat/keš — restringirani radnik ne dosegne skrivenog klijenta.
- SSRF guard na vanjskim fetchevima (samo http/https, blokira privatne/loopback/
  link-local osim `ATLAS_EGRESS_ALLOW`) i **LAN guard** na uređajima (samo
  privatne adrese; loopback OK, link-local/cloud-metadata/IPv4-mapped odbijeni).
- Path-traversal zaštita na NAS operacijama; e-račun auto-sort ne pregazi
  postojeći fajl (uniquify).
- **XML (e-račun/RSS) odbija DTD/entitete** → nema billion-laughs/XXE.
- xlsx izvoz otporan na formula-injection; prirez override ograničen na 0–30%.
- **Sigurnosna zaglavlja** (CSP, X-Frame-Options DENY, nosniff, no-referrer) na
  svim odgovorima; `/docs`/`/openapi.json` isključeni; pred-decode limit tijela.
- **DB + tajna 0600, data_dir 0700**; PII redakcija prije LLM-a kad je uključena.
- **Brisanje klijentskih podataka nije dostupno** (zakonska retencija); promjene
  su praćene SHA-256 hash-chain auditom (append-only, verifikabilan).
- v1 je zamišljen za LAN/plaintext: cookie `Secure` + HSTS uz `ATLAS_HTTPS_ONLY=1`.

## Arhitektura

Jedan SQLite fajl (WAL mod, bez ORM-a), moduli organizirani po domeni —
svaki fajl jedan fokus. `config_overrides` tablica je centralni mehanizam:
watchlist i learn upisuju, svaki modul čita override prije hardcoded
defaulta.

```
atlas/
  core/       spine (baza), llm (provider dispatcher), security (JWT/PII/audit),
              subproc, net (SSRF), optional (degradacija)
  rag/        embed, retrieval (RRF), router, composer, citations, cache,
              sql_lane, selfrag, graphrag, pipeline
  docs/       ingest, ocr, eracun, imap_fetch, pdfforms
  business/   place (plaća), dnevnice, quickref, obveze, kalendar, expiry,
              checklist, notes, auditlog, dashboard, monthly
  knowledge/  kb, translate, features, patterns
  web/        api (FastAPI), watchlist, learn
  ops/        doctor, health, nis2, setup, seeds, evalrun
  browser/    bridge, agent, workflows, sessions
extension/    Chrome MV3 (manifest, background.js, popup)
```
