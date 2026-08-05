# RAGSPINE Setup Wizard — dizajn (2026-08-05)

Status: odobreno za implementaciju (nakon 2 runde adversarial provjere — sec-codex, msg-hermes, msg-kilo2).
Uzor („krađa"): **NousResearch/hermes-agent** `hermes_cli/setup.py` (terminal TUI: modularne
sekcije, `prompt_choice`/`prompt_checklist`/`prompt_yes_no`, `_print_setup_summary`) + gatekeeper
koncept od **AnythingLLM** (`setup_complete` flag, redirect dok nije gotovo).

## 1. Cilj i kontekst

RAGSPINE = lokalni RAG za knjigovodstveni ured (FastAPI+SQLite, LAN, Windows-first). Prvi
puta se pokreće preko **terminala** (bez `.exe` — svjesna odluka: jedan ured, tehnički admin,
`.exe` build-pipeline/potpisivanje = nepotreban teret). Wizard priprema da **WebUI** (svakodnevni
proizvod) proradi: mozak (LLM), operater, mreža/HTTPS, servis.

Ne-ciljevi (svjesno izbačeno iz wizarda → Postavke post-setup): mail, Telegram gateway, VLM OCR,
ostali radnici, vrste obveza, organizacija/OIB.

## 2. Oblik i arhitektura

- **Terminal TUI** `ragspine setup`, jedan fiksni linearni slijed (bez Brzo/Potpuno/Minimalno modova).
- Sekcije = `setup_X(cfg)` funkcije, **reuse i u web Postavkama** (jedna funkcija = i wizard korak i kartica).
- **Trajno stanje + resume:** `system_config` tablica drži `setup_stage` (zadnji dovršeni korak) i
  `setup_complete`. Pad usred wizarda → sljedeći `ragspine setup` nastavlja od `setup_stage`.
- **Gatekeeper (web):** middleware gleda `setup_complete==true`, NE „ima li korisnika". Ako nije
  gotovo i netko otvori WebUI → stranica „pokreni `ragspine setup` u terminalu". (Postojeći
  `web/firstrun.py` gatekeeper se prilagođava da čita `setup_complete`.)
- **Non-interaktivno** (nema TTY): ispiši upute, izađi (kao hermes-agent).

## 3. Stranice (6)

### Stranica 1 — Preduvjeti
Baza: `ops/preflight.py requirements()`.
- **TVRDI minimum (blokira Naprijed):** Python 3.11+, RAM≥prag, disk≥prag, data-dir upisiva
  (write-probe), **Tesseract hrv+eng** (jedini first-run OCR → obavezan, ne warn).
- **STATUS (ne blokira, ✓/⚠):** internet, Ollama (instaliran? servis? verzija?), opc. Python moduli.
- **Mreža rano:** detektiraj je li IP **statički ili DHCP**; ako DHCP → ponudi `netsh interface ip
  set address` (Windows) ili jasno upozori sada (ne na kraju) — inače admin bez router-pristupa
  zapne na zadnjem koraku.
- **Proxy:** polje za HTTP proxy (Ollama pull / pip / kasnije mail idu preko njega).
- **Offline:** internet je STATUS, ne blok. Bez neta: preskoči model-download, ponudi ručne
  download-linkove; `--offline` flag za air-gapped.
- **Auto-install (Windows-first):** izlista što fali; jedan klik `winget install --exact --id <ID>
  --source winget` uz **UAC-potvrdu** + **hash/verzija allowlista** (hardkodirano, bez shell-a).
  Drugi OS (brew/apt/dnf) = prikaži naredbu/link (auto = backlog). Nakon installa **validiraj**
  (`ollama --version`); PATH problem → poruka „restart terminala / ručno dodaj PATH".
- **Upgrade:** ako DB već postoji → detektiraj, ponudi migraciju, ne novi setup.

### Stranica 2 — Operater (admin)
- Polja: korisničko ime, lozinka, ponovi. **Prije modela** (kvar/odustajanje od modela ne ostavlja
  ured bez admina).
- Hash: **PBKDF2-HMAC-SHA256 600k** (bilo 200k; OWASP 2026), migrabilni format (prefiks algo+params).
- **NEMA** maila/org/OIB.
- Kreiranje: **bootstrap-transakcija** (`BEGIN IMMEDIATE`, INSERT prvog admina samo ako nema
  korisnika). **NE** globalni `UNIQUE(owner)` constraint (spriječio bi dodavanje drugih admina poslije).
- Postojeći `web/firstrun.py create_first_owner` je baza; prilagoditi da postavi `setup_stage`.

### Stranica 3 — Model (LLM)
Baza: `ops/preflight.py model_fits()` (fit-pill), prošireni `MODEL_CATALOG`.
- **Ollama spremnost prije svega:** health-check `GET http://localhost:11434/api/tags == 200`
  (ne samo `ollama --version`), floor **≥ 0.5.0**, **auto-start servisa** ako ne radi. Ako
  Ollama nedostupna → grana „preskoči model, postavi kasnije" (ne zaglavi).
- **Bogat katalog** instalabilnih lokalnih modela (Ollama): qwen2.5 3b/7b/14b/32b, llama3.1/3.2,
  mistral, gemma2, phi, deepseek-r1, qwen2.5-coder… Svaki red: **za što je dobar (poredano)** +
  **fit-pill 🟢<50% / 🟡<70% / 🔴** (udio UKUPNOG RAM-a) + **GPU ako VRAM** + **PREPORUKA**.
- **Korisnik bira JEDAN** (ne multi-select) → skidanje (resumable, progress). Više je bila
  zabuna oko aktivnog + napuhan download; korisnik je htio više PRIJEDLOGA, ne instalacija.
- **Embedding:** auto s **fit-pillom + eksplicitnim download+verify** (bge-m3 ne stane na 4 GB →
  fallback mali npr. all-MiniLM). Bez zasebne stranice.
- **Self-test gate:** prompt „Odgovori točno: OK RAGSPINE" → uspjeh = **ne-prazan bounded odgovor
  unutar timeouta**; regex `/OK RAGSPINE/i` = soft-check (ne tvrdi fail). **Timeout prilagođen
  cold-loadu** (prvi load 7B zna >10 s — mjeri/duži timeout), **3 retry**, **Preskoči/Cancel**.
  Kvar NE poništava ostatak setupa.
- OCR prvi run = Tesseract (str.1). VLM = Postavke poslije.

### Stranica 4 — Mreža + HTTPS + servis
- **Bind IP + port** izbor (ne fiksni 8443; provjeri zauzet **ovdje**, ne u str.1; više NIC-ova →
  izbor sučelja) + potvrda statičke adrese (detekcija iz str.1).
- **Servisni račun:** kreiraj/odaberi low-priv Windows račun (`ragspine_svc`) — **prije mapa**, da
  se pristup mapama može testirati baš pod njim.
- **SAN cert** za odabrani IP/hostname (sad se zna). Windows: preferiraj **AD CS/GPO** ako postoji.
  `ragspine trust` distribuira **samo javni CA cert (fingerprint)** — NIKAD CA private key.
- **Windows Service** (autostart/recovery) pod `ragspine_svc`; **ACL** na DB/modele/cert-ključ/
  `secret.key`; **firewall** pravilo za port.
- **Ključevi (Fernet/JWT) izvan DB/backupa:** `secret.key` s **Windows ACL**; DPAPI **LocalMachine**
  scope (CurrentUser pod installer-adminom servis ne bi otvorio) + **eksplicitan export/restore**
  postupak. Upozorenje: servisni račun **bez enforced password-rotacije** (inače DPAPI gubi tajne);
  domena → **gMSA**.

### Stranica 5 — Mape / mrežni pogoni (preskočivo → Postavke)
- Spremaj **UNC putanje** (`\\server\...`), NE drive-letter (servisni račun ih ne vidi).
- Uloge: **Klijenti, Propisi, Zajednički skenovi, Knjigovodstveni program**.
- **SMB kredencijali** ako share traži (`net use \\server\share /user:DOMAIN\user … /persistent:no`);
  lozinka DPAPI-šifrirana. Napomena: lokalni servisni račun + domenski share → Kerberos možda
  nedostupan, NTLM fallback (može biti onemogućen na NAS-u) — **terenski testirati** razne NAS konfige.
- **Test pristupa POD SERVISNIM identitetom** (sad postoji iz str.4).
- Iste uloge idu u web Postavke → Mrežne mape.

### Stranica 6 — Gotovo
- `_print_setup_summary`: sažetak svih stranica (konfigurirano / preskočeno + kako dodati kasnije).
- **Konkretan backup/restore** (ne samo upozorenje): DB lokacija; export/restore `secret.key`+JWT;
  **backup Ollama modela** (`%USERPROFILE%\.ollama\models`, van RAGSPINE dira — bez njih RAG mrtav
  i uz preživjeli DB+ključ); verificiran snapshot (VACUUM INTO, `ops/backup.py`) + provjera restorea
  na drugom stroju. (Napomena: korisnik ima vanjski NAS koji backupira NAS+server; ovdje samo
  ključ+modeli+verifikacija, ne konfiguracija odredišta.)
- Postavi `setup_complete=true`.
- „Pokreni RAGSPINE sada? [Da]" → servis start + otvori **Edge `--app=https://<ip>:port`**
  (čist app-prozor). Puni PWA manifest = backlog; v1 = Edge app-shortcut dovoljno.

## 4. Serviranje + preglednik

- `ragspine serve` (`_cmd_serve`) → **HTTPS** (uvicorn SSL, cert/key iz str.4). Bilo HTTP → mijenja se.
- Preglednik: **Microsoft Edge** (Chromium) preporučeno — predinstaliran na Win, app-mode, sleeping-
  tabs (lakši od Chromea), auto-update, enterprise-upravljiv. Chrome ekvivalent. Firefox ne (bez desktop PWA).
- Prijava: JWT session-cookie (`COOKIE_NAME`), „Zapamti me" (30 d), podesiv idle-timeout.

## 5. Životni ciklus

Server (jednom): install skripta → `ragspine setup` → servis (autostart).
Svi (svaki dan, preglednik): `https://<ip>:port` → prijava → WebUI (chat + SVI podaci + Postavke).
Radnici se NE spajaju kroz wizard; admin ih doda u Postavke → Radnici; ograničeni vide podskup
(pipeline-scoping već postoji).

## 6. Post-setup u Postavkama (WebUI)

- **E-pošta** (`/ui/posta`, postoji): dodaj više mailova; upiši email → pozadinska detekcija
  providera → OAuth (Gmail/M365, public-client PKCE/device-code, bez client-secreta) ili lozinka
  (stari Exchange). Bez Gmail People/kontakata u first-runu (scope-creep).
- **Telegram gateway** (kartica postoji): bot token → getMe → pairing (private-chat only, self-pair,
  TTL, revoke, rate/cost limit — postoji). Rich-tablice (Bot API 10.1 `RichBlockTable`) opcija.
- **OCR-VLM:** nova sekcija Postavke → OCR. Default Tesseract; opcijski **VLM endpoint**
  (`RAGSPINE_OCR_URL`) s katalogom (Unlimited-OCR/HunyuanOCR) + provjera hardvera. Pošteno:
  Unlimited-OCR/HunyuanOCR službeno vLLM/CUDA/Docker → traži GPU; „ukopča se" kad ima hardvera.
- Ostali radnici, vrste obveza (PDV/JOPPD), organizacija/OIB.

## 7. Backlog (izvan ovog speca)

- Telegram preglednik mapa: `RichBlockTable` (nativna vizualna tablica) + inline gumbi + `sendDocument`
  (klik klijent → podmape → otvori file), poštuje ovlasti + traversal-guard.
- Telegram Mini App (treba trusted HTTPS + CA na mobitelu).
- Puni PWA manifest + ikone + service-worker (ne cachea API/PII, samo verzionirane statičke assete).
- Multi-OS auto-install (brew/apt/dnf).

## 8. Otvorena pitanja za implementaciju (ne blokiraju spec)

- Točan floor Ollama verzije (predloženo ≥0.5.0) — provjeriti API kompatibilnost.
- Cold-load timeout self-testa — mjeriti umjesto fiksnog broja.
- gMSA vs lokalni servisni račun — ovisi o tome ima li ured AD domenu (tipično workgroup).
- Točan popis `MODEL_CATALOG` proširenja + per-model „za što je dobro" opisi.

## 9. Verifikacija (runnable check)

Svaka izmjena ostavlja bar jedan test: `tests/test_setup_wizard.py` (stanje/resume, gatekeeper
`setup_complete`, redoslijed koraka, Tesseract-obavezan gate, Ollama-nedostupna grana). Cyrillic-gate
(`tests/test_no_cyrillic.py`) i CI (4 OS) moraju ostati zeleni.
