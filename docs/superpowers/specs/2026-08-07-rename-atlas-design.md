# Rename RAGSPINE → ATLAS — dizajn (2026-08-07)

## Cilj

Proizvod se zove **ATLAS**. Sve vidljivo i sve programsko nosi novo ime;
postojeće instalacije (Nick i buduće) NE smiju puknuti — stara env imena,
stari data dir i stara CLI naredba rade preko aliasa/fallbacka.

## Opseg

1. **Python paket** `ragspine/` → `atlas/` (git mv + sed importa u paketu
   i testovima). `ragspine.egg-info` je gitignoreiran nusprodukt — briše se,
   `pip install -e .` generira `atlas.egg-info`.
2. **pyproject.toml**: `name = "atlas"`; console scripts: `atlas` (primarni)
   **i** `ragspine` (alias, do v2) — oba na `atlas.__main__:main`.
3. **Env varijable**: `ATLAS_*` primarno, `RAGSPINE_*` alias (zauvijek —
   trošak je jedan helper). Helper u `atlas/config.py`:
   `_env("X", default)` čita `ATLAS_X` pa `RAGSPINE_X` pa default.
   Svi moduli koji čitaju env direktno (config, rag/embed, ops/wizard,
   ops/winsvc, __main__, web/templates_mape) idu kroz helper ili dual-read.
   Wizard koji POSTAVLJA env (mount roots za serve) postavlja OBA imena.
4. **Data dir**: default `~/.atlas`; fallback (zauvijek): ako env ne kaže
   drukčije i `~/.atlas` ne postoji, a `~/.ragspine` postoji → koristi
   `~/.ragspine`. BEZ automatskog premještanja (server može biti živ;
   premještanje baze = rizik gubitka podataka). Isti obrazac za ime baze:
   postojeći `ragspine.db` u data diru se koristi; novi installi dobiju
   `atlas.db`.
5. **Cookie**: `ragspine_token` → `atlas_token`, bez aliasa — posljedica je
   jednokratna ponovna prijava.
6. **HTTP identitet**: `Server: ATLAS` header, `User-Agent: ATLAS/1.0`.
7. **Poruke i naslovi**: wizard (TUI), web UI templatei, README, install.sh,
   install.ps1, extension/ (manifest, popup, background), aktivni docs
   (DEPLOY_URED, WINDOWS_COMPAT, F_WIZARD...). Povijesni docs
   (docs/superpowers/specs|plans, e2e-nalazi) se NE diraju.
8. **Imena servisa u dokumentaciji**: `RAGSPINE-serve` → `ATLAS-serve` u
   uputama (postojeći Scheduled Task na Nicku se briše po planu brisanja
   Nicka, ne diramo ga sad).
9. **GitHub repo ime**: NE diramo. Upute za ručni rename + `git remote
   set-url` idu u `docs/RENAME_REPO.md`.

## Što ostaje kompatibilno

| Stvar | Trajanje | Mehanizam |
|---|---|---|
| `RAGSPINE_*` env | zauvijek | `_env` helper fallback |
| `~/.ragspine` data dir | zauvijek | fallback ako postoji, a `~/.atlas` ne |
| `ragspine.db` ime baze | zauvijek | fallback ako postoji |
| `ragspine` CLI | do v2 | drugi console script u pyproject |
| `ragspine_token` cookie | ne | odmah novo ime (re-login) |

## Audit starog imena (obavezan završni korak grane)

a) `grep -ri ragspine` preko repoa = NULA pogodaka izvan iznimaka.
b) Trajni test `tests/test_rename_audit.py`: grepa `ragspine` (case-insensitive)
   po KODU — `atlas/**`, `tests/**`, `install.sh`, `install.ps1`,
   `pyproject.toml`, `extension/**` — i PADA ako nađe pogodak na retku bez
   markera `compat`. Svaki namjerni ostatak starog imena u kodu nosi
   `compat` u retku (komentar u Pythonu/PS/sh, ili je u whitelistu testa).
c) Popis iznimaka (unaprijed):
   - kompatibilnosni retci s markerom `compat`: env aliasi u config helperu
     i dual-read mjestima, `.ragspine`/`ragspine.db` fallback, `ragspine`
     console-script alias u pyproject, dual-set mount roots u wizardu
   - `tests/test_rename_audit.py` sam + testovi kompatibilnosnih fallbacka
     (marker `compat` u retku)
   - povijesni docs: `docs/superpowers/**`, `docs/e2e-nalazi-2026-08-06.md`,
     `docs/NEXT_SESSION_SECURITY.md`
   - `docs/RENAME_REPO.md` i README redak "ranije RAGSPINE" + git remote URL
     (dok korisnik ne preimenuje repo na GitHubu)
   - git povijest (`.git/`), gitignoreirani artefakti (`*.egg-info`),
     `.kilo/`, `AGENTS.md` (Kilo infrastruktura, izvan proizvoda)

## Rizici i redoslijed sigurnih koraka

1. git mv paketa + sed importa (mehanički, atomski commit) — suite mora
   proći odmah nakon.
2. pyproject + reinstall u dev okolini.
3. Config helper + data dir/db fallback (s testovima fallbacka).
4. Cookie/headeri/UA (s update testova).
5. Stringovi (wizard/web/README/install/extension/docs).
6. Audit test + finalni grep + RENAME_REPO upute.

Testovi postojeći: 516 pogodaka u tests/ — sed rješava importe i env imena;
testovi koji ciljano testiraju fallback pišu se novi.

## Ne-ciljevi

- GitHub repo rename (ručno, korisnik).
- Deploy na Nicka (briše se poslije TUI grane).
- WinSW/NSSM, cloud LLM, chat lane redizajn (izvan opsega po prompt zadatku 3).
