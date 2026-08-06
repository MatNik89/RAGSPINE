# Setup wizard — upgrade grana za postojeću bazu (2026-08-06)

Status: odobreno (autonomna odluka po delegaciji; okvir iz zadatka: implicitni preskok vs.
eksplicitna ponuda — odabrana eksplicitna ponuda).

## 1. Problem

Spec `2026-08-05-setup-wizard-design.md` (Stranica 1): „Upgrade: ako DB već postoji →
detektiraj, ponudi migraciju, ne novi setup." Nije bilo eksplicitne ponude. Instalacija
koja je radila PRIJE wizarda ima bazu s adminom, modelom, mrežom... ali bez
`setup_complete` flaga.

Napomena o interakciji s postojećom migracijom: `_migrate_setup_complete_for_upgrades`
(`core/spine.py`) već kod otvaranja baze (unutar `init_spine`) tiho postavi
`setup_complete=true` čim baza ima bar jednog korisnika a flag nedostaje — to se odvije
PRIJE nego `wizard.run()` uopće krene, pa `_cmd_setup` (`__main__.py`) odmah ispiše
„Setup je već dovršen" i izađe. Zato eksplicitna ponuda iz ovog dokumenta realno okida
samo kroz `--reset` (reset se izvodi NAKON `init_spine` u istom procesu, dakle prije nego
migracija ponovno stigne postaviti flag) — i kroz svaki budući put koji stigne u `run()`
sa stage 0, korisnicima i bez complete flaga.

## 2. Što „migracija" znači

Shema baze se ne mijenja (config_overrides, users, folders postoje i rade). Nema podataka
za seljenje. „Migracija" = **preuzmi postojeću konfiguraciju**: pokaži što je detektirano
(postojeći `render_summary`), i na potvrdu označi `setup_complete` — bez prolaska kroz
stranice. Odbijanje = normalni wizard od početka (stranice već toleriraju postojeće
stanje: admin-preskok postoji, model/mreža nude ponovni unos).

Razmotrena alternativa — samo implicitni preskok po stranicama (proširiti admin-preskok
na model/mrežu/mape): odbačeno jer korisnik i dalje prolazi svih 6 stranica, a spec
izričito traži ponudu; k tome bi „preskoči ako postoji" na modelu/mreži onemogućio
svjesnu promjenu vrijednosti kroz wizard.

## 3. Ponašanje

Nova funkcija `page_upgrade(spine, cfg, *, input_fn, out) -> bool` u `ops/wizard.py`
(vraća True = konfiguracija preuzeta, setup gotov; False = nastavi normalni wizard):

1. Header: „Postojeća baza otkrivena".
2. `render_summary(spine, cfg)` — što je detektirano (admin, model, embedding, mreža,
   cert, mape).
3. `prompt_yes_no("Preuzmi postojeće postavke i označi setup dovršenim? (ne = prođi setup)")`
   — default **da** ako su admin + LLM model + mrežni host svi postavljeni (klasična
   legacy instalacija), inače default **ne** (npr. admin kreiran kroz web, ostalo prazno).
4. Na „da": vrati True. Na „ne": vrati False.

U `run()`: nakon `is_complete` provjere, **okidač**: `stage == 0 and not
firstrun.needs_onboarding(spine)` (korisnici postoje = jedini pouzdan signal postojeće
baze; svježa baza nema korisnika, a resume od stage>0 nije upgrade slučaj). Ako
`page_upgrade` vrati True → `mark_complete`, poruka
„✓ Postojeća konfiguracija preuzeta — setup dovršen.", `launch_now`, return.
Inače nastavi postojeći slijed stranica.

Rubni slučajevi:
- `--reset` na dovršenom sustavu → stage 0, korisnici postoje → ponuda se pojavi; to je
  ispravno (reset pa „preuzmi" = brzi povratak; tko želi ispočetka, odgovori „ne").
- Non-TTY: `page_upgrade` je unutar postojećeg try/except (EOFError/KeyboardInterrupt) u
  `run()` — ista poruka o interaktivnom terminalu, stanje netaknuto.
- `page_gotovo` (backup ponuda) se NE vrti u upgrade grani — sažetak je već prikazan, a
  backup postoji kao `ragspine backup`; manje trenja za povratnika.

## 4. Testovi

- `page_upgrade`: „da" vraća True; „ne" vraća False; default „da" kad su admin+model+host
  postavljeni, default „ne" kad nešto od toga fali (provjera kroz prazan input).
- `run()` integracija: legacy baza (admin postoji, stage 0) → ponuda se pojavi, „da" →
  `is_complete` True i stranice se NE izvode; „ne" → stranice idu normalno.
- Svježa baza (bez korisnika) → ponude NEMA, wizard ide od stranice 1.
- Resume (stage ≥ 1) → ponude NEMA.

Bez mreže, stdina, pravih subprocessa (postojeći `_reader`/`out=lines.append` obrasci).
