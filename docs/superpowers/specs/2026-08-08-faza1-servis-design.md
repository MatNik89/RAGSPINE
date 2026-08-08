# Faza 1 — pravi servis (WinSW / systemd) — dizajn (2026-08-08)

## Cilj

Atlas radi 24/7: preživi zatvaranje terminala, odjavu i restart stroja;
sam se digne s bootom. Rješava E2E KRITIČNO (detached serve umire sa
zatvaranjem terminala; python zombi nakon schtasks /End servira stari
kod; tihi crash bez loga).

## Odluke

- **Windows: WinSW wrapper** (winsw/winsw, Apache-2.0) — jedan .exe koji
  konzolnu aplikaciju prevede u pravi SCM servis. NSSM odbačen (stariji,
  bez službenih buildova). WinSW se SKIDA pri instalaciji servisa
  (GitHub release, injektabilan urlopen — obrazac kao hrv.traineddata),
  ne dodaje se kao Python ovisnost.
- **Linux/macOS: systemd unit** (postojeći predložak u winsvc.py,
  dorađen) — zapisati datoteku kad imamo prava, inače ispisati naredbe.
- **Log umjesto DEVNULL**: serve pod servisom I launch_now pišu izlaz u
  `<data_dir>/logs/serve.out.log` / `serve.err.log` (WinSW log roll;
  launch_now otvara datoteke umjesto DEVNULL) — tihi crash postaje
  čitljiv (E2E nalaz).
- Servisno ime ostaje `ATLAS`; postojeći firewall/ACL koraci iz
  winsvc.py ostaju i izvršavaju se u istom toku.

## Izvedba

### 1. `atlas/ops/winsvc.py` proširenje

- `WINSW_URL` (github release, x64) + `download_winsw(dest, *, urlopen, out) -> bool`
  (idempotentno: postojeći exe = preskoči).
- `winsw_xml(exe, data_dir, port) -> str` — čista funkcija, generira
  `atlas-service.xml`:
  - `<executable>` = atlas exe, `<arguments>serve</arguments>`
  - `<env name="ATLAS_DATA_DIR" .../>`
  - `<log mode="roll-by-size">` u `<data_dir>/logs` (sizeThreshold ~10MB,
    keepFiles 4)
  - `<onfailure action="restart" delay="5 sec"/>` ×3 pa none
  - `<stoptimeout>15 sec</stoptimeout>` — WinSW na stop ubija CIJELO
    procesno stablo (rješava zombi nalaz)
  - `<serviceaccount><username>NT AUTHORITY\LocalService</username></serviceaccount>`
- `install_service(...)` novi tok na Windowsu:
  1. `service_dir = <data_dir>/service`; download_winsw → `atlas-service.exe`
  2. zapiši `atlas-service.xml` (winsw_xml)
  3. run_isolated: `atlas-service.exe install` pa `atlas-service.exe start`
  4. postojeći firewall + icacls koraci (data_dir ACL mora pokriti i
     logs/ — pokriva, jer je unutar data_dir)
  5. uspjeh → True + ispis statusa + BIOS napomena: "Za auto-start
     nakon nestanka struje uključi u BIOS-u 'Restore on AC Power'
     (jednokratno, na ovom stroju)."
  - pad bilo kojeg koraka → poruka + False (bez pola-instaliranog
    stanja gdje je moguće: install pao → obriši xml? ne — ostavi za
    dijagnozu, poruka kaže što provjeriti)
- `uninstall_service(*, out) -> bool`: `atlas-service.exe stop` +
  `uninstall` (ako exe postoji); ne-Windows: systemd upute.
- `service_status(*, out) -> str`: `sc.exe query ATLAS` parsiranje
  (RUNNING/STOPPED/nepostojeći); ne-Windows: `systemctl is-active atlas`.
- Linux grana `install_service`: pokušaj zapisa
  `/etc/systemd/system/atlas.service` (PermissionError → ispiši unit +
  naredbe kao sad); uspjeh → `systemctl daemon-reload` + `enable --now`
  kroz run_isolated; vrati True/False po ishodu.

### 2. CLI: `atlas servis <install|uninstall|status>`

Nova subkomanda u `atlas/__main__.py` (hrvatski naziv, dosljedno CLI-ju):
poziva gornje; install treba admin/root — poruka ako nije elevated
(Windows: ctypes.windll.shell32.IsUserAnAdmin; POSIX: os.geteuid).

### 3. Wizard (stranica mreže) — postojeće pitanje "Instaliraj kao
servis (autostart)?" sada zove novi tok; poruka o uspjehu/padu jasnija.
launch_now: kad servis POSTOJI i vrti se (service_status == running),
ne pokreći detached kopiju — ispiši da servis već radi + URL.

### 4. launch_now log fix (E2E): `_detached_kwargs` stdout/stderr u
`<data_dir>/logs/serve.out.log` / `serve.err.log` (append mode, otvori
datoteke; data_dir dostupan pozivatelju — proslijedi). DEVNULL ostaje
samo za stdin.

## Testabilnost

- winsw_xml: čisti string testovi (executable, log dir, stoptimeout,
  LocalService).
- download_winsw: injektiran urlopen (kao ensure_traineddata testovi).
- install/uninstall/status: run_isolated monkeypatch — sekvence komandi
  se asertiraju; elevacija mockana; bez pravih subprocessa.
- Linux grana: tmp_path kao /etc cilj kroz parametar (path injektabilan
  za test); PermissionError grana.
- launch_now: popen mock već postoji — asertiraj da stdout ide u
  datoteku (kwargs), ne DEVNULL.

## Ne-ciljevi

- DPAPI/custom servisni račun (postojeći backlog, LocalService dovoljan).
- Nick deploy (Nick se briše prije čiste probe).
- UPS logika (faza 4) — ovdje samo temelj koji ona koristi.
