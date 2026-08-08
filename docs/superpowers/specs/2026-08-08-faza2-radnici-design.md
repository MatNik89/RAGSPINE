# Faza 2 — radnici, uređaji, aktivacijski login — dizajn (2026-08-08)

Korisnikov tok (dogovoren doslovno, orkestrator-faze.md):

## 1. Aktivacijski login (radnik prvi put)

- Admin u Postavkama doda radnika SAMO s korisničkim imenom — bez
  šifre: `users.pw_hash = NULL` = stanje "čeka aktivaciju".
- Radnik na /login upiše samo svog usera → Dalje: POST /login/step
  vrati {state: "activate"} kad je pw_hash NULL (bez otkrivanja
  postoje li useri: nepoznat user → {state: "password"} kao i
  aktiviran — protiv enumeracije; na password koraku nepoznat user
  dobiva standardni "krivi podaci").
- Ekran "Dobrodošao, <ime> — postavi svoju šifru": dva polja (šifra +
  ponovi; min 8; moraju se poklopiti; provjere i klijentski i
  serverski). POST /login/activate {user, pw, pw2}: dopušten SAMO kad
  je pw_hash NULL (utrka/replay → 409); uspjeh = postavi hash + odmah
  prijavi (cookie) + audit "user aktiviran".
- Login stranica ostaje dvokoračna i za aktivne (user → šifra) ili
  jednokoračna? ODLUKA: dvokoračna za sve (user pa šifra/aktivacija) —
  jednostavniji tok, korisnikov opis ("upiše ime i klikne dalje").
  templates_login se prilagođava minimalno (bez redizajna — korisnik
  radi dizajn); postojeći POST /login (user+pw odjednom) OSTAJE za
  kompatibilnost (API/testovi).

## 2. Admin-kao-radnik

- Na password koraku: ako verify_password(pw, user.pw_hash) padne,
  probaj sve OWNER/ADMIN hasheve: verify protiv admin lozinke. Uspjeh →
  prijava kao TAJ radnik, ali audit: "admin <admin_user> ušao kao
  <user>" + JWT claim {impersonated_by: <admin_user>}.
- NE vrijedi za aktivaciju (admin ne može aktivirati umjesto radnika
  kroz ovaj put — koristi reset pa radnik sam postavlja; admin ulaz u
  NEAKTIVIRANOG radnika: dopušten admin šifrom? ODLUKA: DA — admin
  smije ući i u neaktiviran račun (podešavanje prije predaje
  radniku); audit isto bilježi).
- Cijena: login sada radi do N verify poziva za admin račune — N je
  malen (1-2 admina), prihvatljivo; dummy-hash timing obrazac ostaje.

## 3. Postavke → Radnici (web)

- Nova stranica /ui/radnici (admin-only): tablica (user, uloga, stanje:
  aktivan/čeka aktivaciju, uređaj ako je vezan), forma "Dodaj radnika"
  (user + uloga radnik/admin), akcije: "Resetiraj šifru" (pw_hash →
  NULL + audit; radnik prolazi aktivaciju ispočetka), "Ukloni".
- API: GET /radnici, POST /radnici, POST /radnici/{id}/reset,
  DELETE /radnici/{id} — sve admin-only (postojeći require obrasci),
  audit na sve. Vlasnika (owner) ne smije se resetirati/uklanjati osim
  ako to radi sam owner.
- Postojeći /users API (ako postoji) ostaje; novi API je UI sloj nad
  istom users tablicom.

## 4. Postavke → Uređaji (proširenje postojećeg)

- devices tablica se proširuje (ADITIVNO — migracija _ensure_columns):
  `caps TEXT` (JSON: {"shutdown_order": int|null, "wol": bool,
  "run_programs": bool, "monitor_only": bool}), `mac TEXT` (za WOL),
  `worker_username TEXT` (vezanje uređaj↔radnik), `host TEXT` (IP/ime).
- Novi kind: "radna-stanica" (uz postojeće scanner/printer vrste).
- UI /ui/uredaji (proširenje postojeće devices stranice ili nova
  sekcija): dodavanje radne stanice (ime, host, MAC opcionalno),
  kvačice sposobnosti, padajući izbornik radnika (iz users), redni
  broj gašenja. Mrežno skeniranje = postojeći discovery gumb ostaje
  kao pomoć (prijedlozi), ništa se ne dodaje samo.
- business/devices.py: add/update prošireni za nova polja; helper
  `device_for_worker(spine, username)` (faza 5 ga koristi).

## Sigurnosne odluke

- Aktivacija bez enumeracije usera (gore).
- pw_hash NULL nikad ne prolazi verify (verify_password guard na
  NULL/prazno → False, provjeri postojeću implementaciju).
- Admin-kao-radnik NE povisuje ovlasti: sesija nosi rolu RADNIKA
  (admin vidi što i radnik — točno korisnikov zahtjev "za svakog
  radnika ukucati njegovog usera"), impersonated_by samo za audit.
- Reset šifre: postojeće sesije radnika ostaju do isteka JWT-a (24h)
  — prihvatljivo za ured; zabilježiti u docs.

## Testabilnost

Sve kroz postojeće obrasce: TestClient FastAPI (postojeći test_api_auth
stil), spine fixture, bez mreže. Login tokovi: aktivacija happy/utrka/
prekratka šifra/nepodudaranje; enumeracija (isti odgovor za nepoznatog
i aktivnog); admin-kao-radnik (uspjeh+audit+claim; kriva šifra); reset;
uređaji CRUD + vezanje + caps roundtrip; migracija starih baza
(devices bez novih stupaca).

## Ne-ciljevi

- Dizajn/izgled login ekrana (korisnik radi; ovdje minimalan HTML u
  postojećem stilu).
- Agent/WOL izvršenje (faza 5) — ovdje samo podaci o uređajima.
- Slanje mailova/pozivnica radnicima.
