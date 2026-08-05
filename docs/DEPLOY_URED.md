# RAGSPINE — postavljanje u uredu (LAN), korak po korak

Vodič za **prvi install u knjigovodstvenom uredu**: lokalni RAG na jednom
računalu (Raspberry Pi / mini PC), pristup preko LAN-a, jedan install po firmi.
Prati redom.

## 0. Preduvjeti

- Python 3.11+ (`python3 --version`).
- Disk za NAS mapu `KLIJENTI` (SMB mount) ako se koriste uredske funkcije.
- LLM: ili `claude` CLI (OAuth), ili lokalni Ollama, ili API ključ providera.

## 1. Instalacija

Kloniraj repo pa pokreni skriptu za svoj OS (venv + install + model + operater):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1      # Windows
```
```bash
./install.sh                                                # Linux / macOS
```

Ručno: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[full]"`.
Skripta kreira operatera; ako ideš ručno, korak 3 niže.

## 2. Tajne i okolina

RAGSPINE sam generira JWT tajnu (`~/.ragspine/secret`, 0600) pri prvom pokretanju.
Za produkciju postavi eksplicitno + zaključaj okolinu:

```bash
export RAGSPINE_DATA_DIR=/var/lib/ragspine          # DB + tajna + modeli (chmod 0700 auto)
export RAGSPINE_JWT_SECRET="$(openssl rand -hex 32)" # ili pusti auto-generiranje
export RAGSPINE_MOUNT_ROOTS=/mnt/nas                  # dozvoljeni korijeni mrežnih mapa
export RAGSPINE_HOST=0.0.0.0 RAGSPINE_PORT=8400       # LAN vidljivost
```

- **DB i tajna su 0600, data_dir 0700** — drže sav klijentski PII, pbkdf2
  hasheve i JWT tajnu. Ne stavljaj ih u mapu čitljivu drugim lokalnim korisnicima.
- `RAGSPINE_MOUNT_ROOTS` je *jedini* korijen ispod kojeg se smiju registrirati/
  čitati mape — bez njega su NAS funkcije isključene (sigurno zadano).

## 3. Operater (admin) i radnici

```bash
ragspine auth add ana            # kreira korisnika (upit za lozinku) → prvi je owner
```

- Prvi korisnik = **owner** (puni pristup). Dodatne radnike dodaje owner kroz UI
  (Postavke → Radnici) ili `ragspine auth add`.
- **Vidljivost klijenata po radniku**: zadano radnik vidi SVE klijente. Za
  ograničenje (radnik vidi samo svoje) — Postavke → Radnici → vidljivost. Tada
  su njegovi RAG upiti, bilješke, obavijesti, e-poruke i kartoni ograničeni na
  dodijeljene klijente (uredski dokumenti bez klijenta ostaju svima vidljivi).

## 4. KLIJENTI mapa i dogovor strukture

1. Registriraj postojeću NAS mapu `KLIJENTI` kao `role='klijenti'`
   (Postavke → Mape). Mora ležati ispod `RAGSPINE_MOUNT_ROOTS`.
2. **Ne nameći strukturu** — dogovori je kroz chat s RAGSPINE-om
   (lane „arhitektura", admin-only): npr. „dogovor mape po klijentu: Osobni
   dokumenti, Ugovori, Izvodi". RAGSPINE pamti dogovoreno i tek onda predlaže/
   kreira mape koje nedostaju.
3. Otkrivanje postojećih klijenata: Postavke → Uvoz/Otkrivanje (admin-only).

## 5. Uređaji (skeneri/pisači)

- Postavke → Uređaji → **Pronađi** (mDNS discovery), pa **Dodaj** (admin-only).
- Uređaji moraju biti na **privatnoj LAN adresi** (10./172.16./192.168.). Loopback,
  link-local i cloud-metadata adrese su odbijene (SSRF guard).
- Radnik pri skeniranju/printanju **bira** uređaj.

## 6. Praćenje (ključne riječi + izvori)

- Postavke → Praćenje: ključne riječi ureda (max 100), watch-izvori (RSS/HTML).
- Prirez se auto-preuzima iz praćenih izvora samo unutar 0–30% (guard protiv
  trovanja lažnim/MITM izvorom); izvan granica se ignorira.
- Excel izvoz je otporan na formula-injection (vrijednosti s `= + - @` / TAB/CR
  se neutraliziraju).

## 7. Provjera prije puštanja

```bash
ragspine doctor
```

Mora biti ✓ na: `python_version`, `disk_space`, `db_writable`, `perms` (0600),
`korisnici` (barem jedan), `llm_provider`. Informativno: `luks` (poželjna
disk-enkripcija za PII at-rest), `nas` (registrirana KLIJENTI mapa), `ollama`/
`ocr_server` (ako se koriste).

## 8. Mrežna izloženost (bitno za produkciju)

- **HTTPS**: iza reverse-proxyja (Caddy/nginx) na LAN-u. Uz HTTPS uključi
  `RAGSPINE_HTTPS_ONLY=1` — tada cookie dobiva `Secure` + šalje se HSTS.
- **Limit veličine tijela**: aplikacija odbija zahtjeve s `Content-Length`
  > 64MB, ali chunked/streaming bez headera hvata tek reverse-proxy — postavi
  `client_max_body_size 64m` (nginx) / ekvivalent.
- Interaktivni API docs (`/docs`, `/openapi.json`) su **isključeni** u aplikaciji.
- Sigurnosna zaglavlja (CSP, X-Frame-Options DENY, nosniff, no-referrer) šalju se
  automatski na svim odgovorima.

## 9. Brisanje podataka

RAGSPINE **namjerno nema funkciju brisanja klijentskih podataka**. Knjigovodstvena
dokumentacija podliježe zakonskoj retenciji (Zakon o računovodstvu / porezni
propisi — čuvanje godinama), pa se podaci klijenata **ne smiju brisati** na
zahtjev. GDPR pravo na zaborav ne poništava zakonsku obvezu čuvanja.
