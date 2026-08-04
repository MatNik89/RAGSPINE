# G — Praćenje propisa: UI + ključne riječi + Excel izvoz

Watchlist backend (hash-diff izvora, law diff, upcoming_changes, notifikacije)
postoji od ranije — G mu daje lice i dvije nove sposobnosti.

## Ključne riječi ureda

Data-driven (config_overrides `watchlist/keywords`), NE hardkod — ured dodaje
svoje pojmove kroz UI. Pri promjeni izvora pogodak se traži u **diffu**
(u onome što se promijenilo, ne cijelom dokumentu) uz fold dijakritika;
pogodak → zasebna `keyword_hit` obavijest. INDUSTRY_KEYWORDS ostaju kao
dopuna za RSS matching.

## Excel izvoz

`GET /watchlist/export.xlsx` (openpyxl, [full] ovisnost — bez nje 503 s
uputom): tri lista — Nadolazeće promjene (datum stupanja + opis + izvor),
Rokovi (kalendar 60 dana), Izvori.

## UI `/ui/pracenje` (nav "Praćenje")

- ključne riječi kao chipovi (dodaj/ukloni)
- nadolazeće promjene + "Provjeri sada" (run) + "Izvoz u Excel"
- izvori: lista s uključi/isključi (soft toggle — povijest se čuva) + dodavanje
  (URL, kategorija, page|rss)

Postojeće watchlist rute prebačene na `require_user_web` (cookie + Bearer)
da UI radi; nove: `/watchlist/upcoming`, `/watchlist/keywords` (GET/POST),
`/watchlist/sources/{id}/toggle`, `/watchlist/export.xlsx`.
