# ATLAS installer — opcija B (postavljanje kod klijenta)

Jedan installer, uloga na prvom ekranu. **Server se instalira PRVI** (iznuđeno:
radna stanica ide dalje tek kad `/health` servera vrati `atlas=true` +
`setup_complete=true`).

## ATLAS-strani hook (u repou, testirano CI-jem)
`GET /health` (javno, bez autha) vraća:
```json
{"status":"ok","atlas":true,"version":"1.0.0","setup_complete":false,"missing":[]}
```
Installer to koristi za "server prvi" provjeru + monitoring. `__version__` u
`atlas/__init__.py` usklađen s `pyproject.toml` (test to čuva).

## Build installera (na Windowsu — Pi ne kompajlira Inno)
Jedna naredba (treba Python 3.11+ i Inno Setup 6 / `ISCC.exe` u PATH-u):
```powershell
powershell -ExecutionPolicy Bypass -File windows\build.ps1
```
Skripta: gradi `atlas` wheel → sastavi embedded Python + pip + `atlas[full]` u
`windows\payload\python` → `ISCC windows\atlas-setup.iss`. Izlaz:
`windows\Output\atlas-setup.exe`.

Post-install server otvara nadzornu ploču kroz `atlas open` (sam izračuna URL iz
host/port + cert SAN — ne nagađa port).

## Kod klijenta
### Glavno računalo (server) — PRVO
1. Pokreni `atlas-setup.exe` → odaberi **Glavno računalo**.
2. Installer registrira ATLAS Windows servis + otvori nadzornu ploču.
3. Dovrši ATLAS setup wizard (modeli, mape…) — tek tad je `setup_complete=true`.
4. Jednokratno u BIOS-u: "Restore on AC" (ATLAS ispiše uputu) da se server sam digne.
5. Za čisti "izgleda kao app" bez browser-warninga: ubaci `cert.pem` u trust store:
   `certutil -addstore Root <put_do>\cert.pem` (i na svakoj radnoj stanici).

### Radna stanica (agent) — NAKON servera
1. Na serveru: **Postavke → Uređaji** → dodaj stanicu → **izdaj token**.
2. Na radnoj stanici pokreni `atlas-setup.exe` → **Radna stanica** → upiši
   adresu servera (`https://IP:8443`) + token.
3. Installer provjeri server (`/health`), pa registrira agenta (autostart pri prijavi).
4. Radnik radi u browseru (PWA "Instaliraj ATLAS" za app-osjećaj).

## Otvoreno (follow-up)
- **mDNS objava** servera (`_atlas._tcp`) za AUTO-pronalazak (sad se adresa upisuje
  ručno; `/health`-provjera već čini "server prvi" iznuđenim).
- **Samo-prijava + odobri**: agent se javi kao "na čekanju", owner klikne Odobri
  (umjesto ručnog tokena). Sad je token ručni (iz Postavke → Uređaji).
