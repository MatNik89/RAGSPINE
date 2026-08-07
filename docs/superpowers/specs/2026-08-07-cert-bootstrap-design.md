# Cert bootstrap + prijateljsko ime — dizajn (2026-08-07)

## Problem (E2E nalazi)

1. `atlas trust` traži terminal i CLI — na klijentskim računalima CLI NE
   POSTOJI; radnik ne može instalirati cert bez admina za tipkovnicom.
   Browser ne smije sam instalirati cert (sigurnosna zabrana po dizajnu).
2. Uputa na stranici mreže ("Na klijente ga instaliraj naredbom: atlas
   trust") je neizvediva — bug.
3. URL s IP-em (https://192.168.178.38:8443) je ružan i lomljiv;
   Fritz!Box daje `<hostname>.fritz.box`, Windows mDNS `<hostname>.local`
   — cert i upute trebaju prijateljsko ime.

## Rješenje

### 1. Prijateljska imena u SAN-u (wizard, stranica mreže)

Novi helper `certs.friendly_names() -> list[str]`:
- `socket.gethostname()` (kratko ime, lowercase)
- `<hostname>.local` (mDNS, radi na Windows 10+/macOS/Linux s Avahi)
- `<hostname>.<dns-sufiks>` ako OS ima DNS sufiks (fritz.box i sl.):
  čitaj `socket.getfqdn()` — ako vrati nešto s točkom različito od
  hostname.local, uzmi; bez mreže/sufiksa → bez unosa. Bez vanjskih
  poziva, čisti stdlib, sve u try/except (nikad ne ruši).
- dedup, lowercase; uvijek uključi i "atlas.local" (postojeće ponašanje
  — kompatibilnost s već izdanim uputama).

`page_mreza`: `generate_self_signed(..., hostnames=friendly_names())`;
ispis servera koristi PRVO ime s točkom (FQDN varijanta) kad postoji:
"✓ Server će služiti na https://nick.fritz.box:8443 (i https://IP:8443)".
Ispravak upute (bug #2): "Na OVOM računalu: atlas trust. Radnici: nakon
pokretanja servera otvore http://<ime>:8080/postavi (bootstrap stranica)."
NAPOMENA kompatibilnost: postojeći certovi se NE regeneriraju (postojeće
`_warn_if_san_stale` ponašanje); novi installi dobiju puni SAN.

### 2. HTTP bootstrap server (uz serve)

Mali stdlib `http.server` thread unutar `atlas serve` (bez novih depsa,
bez drugog uvicorna):
- Novi modul `atlas/web/bootstrap_http.py`:
  `start_bootstrap_server(cert_path, https_url, host, port=8080) -> threading.Thread | None`
  - daemon thread, `ThreadingHTTPServer`; greška binda (port zauzet) →
    log + None (serve nastavlja normalno — bootstrap je pomoć, ne uvjet)
  - rute (samo GET, sve ostalo 404):
    - `/postavi` — HTML stranica (hrvatski, bez JS ovisnosti): naslov,
      3 koraka, gumb-link "1. Preuzmi postavljanje" (→ /postavi-vezu.bat),
      "2. Dupli klik na preuzetu datoteku → Da (UAC)",
      "3. Otvori <https_url>" + napomena da prečac/bookmark rade nakon
      toga; i mala sekcija "Ručno (napredno)": download /cert.pem +
      certutil naredba.
    - `/postavi-vezu.bat` — generirani batch (CRLF!): `@echo off`,
      `certutil -addstore -f Root "%~dp0cert.pem"` NE — bat mora sam
      skinuti cert? Jednostavnije: bat s ugrađenim certom nije trivijalan;
      umjesto toga bat koristi certutil -urlcache za download pa import:
      ```
      @echo off
      echo Postavljam sigurnu vezu za ATLAS...
      certutil -urlcache -split -f "http://<host>:<port>/cert.pem" "%TEMP%\atlas-cert.pem"
      certutil -addstore -f Root "%TEMP%\atlas-cert.pem"
      if errorlevel 1 (echo GRESKA - pokreni kao administrator & pause & exit /b 1)
      echo Gotovo! Otvaram <https_url>
      start "" "<https_url>"
      ```
      (dvoklik na .bat NE eleva sam — certutil -addstore bez admina pada;
      poruka kaže: desni klik → Pokreni kao administrator. Stranica
      /postavi to piše u koraku 2.)
    - `/cert.pem` — javni cert (Content-Type application/x-pem-file)
    - `/` — redirect 302 na /postavi
  - Content-Type/charset utf-8 za HTML; bat kao application/octet-stream
    s CRLF završecima.
- `_cmd_serve`: prije uvicorn.run, ako postoji cert → pokreni bootstrap
  thread na istom host bindu, port iz `ATLAS_BOOTSTRAP_PORT` env (default
  8080; "0" = isključeno). Ispiši "Bootstrap za radnike:
  http://<ime-ili-ip>:8080/postavi".

### 3. Uputa za radnike (wizard, stranica 5/5 Gotovo)

`page_gotovo` nakon backup sekcije ispiše doslovnu uputu (spremnu za
print/mail), s najboljim imenom (FQDN > .local > IP):
```
Uputa za radnike (kopiraj u mail):
  1. Otvori: http://nick.fritz.box:8080/postavi
  2. Klikni "Preuzmi postavljanje" pa desni klik na preuzeto →
     Pokreni kao administrator → Da
  3. Ubuduće koristi: https://nick.fritz.box:8443 (spremi u favorite)
```

## Testabilnost

- friendly_names: monkeypatch socket.gethostname/getfqdn — čisto.
- bootstrap_http: handler logika testira se kroz ThreadingHTTPServer na
  port 0 (ephemeral) + urllib prema 127.0.0.1 — to je localhost loopback,
  dopušteno (postojeći testovi već rade port_free provjere na localhostu);
  bat sadržaj/CRLF/HTML string asercije.
- page_mreza/page_gotovo: postojeći obrasci (injektirani out, mock certs).
- _cmd_serve integracija: mock start_bootstrap_server, bez pravog uvicorna.

## Ne-ciljevi

- QR kod (nalaz ga spominje kao "ili" — tekstualna uputa dovoljna; QR bi
  tražio novu ovisnost).
- Auto-elevacija .bat (UAC iz batcha = hack; jasna uputa umjesto toga).
- macOS/Linux klijenti (ured je Windows; cert.pem + ručna uputa postoje).
- AD CS/GPO domenski certovi (postojeći backlog).
