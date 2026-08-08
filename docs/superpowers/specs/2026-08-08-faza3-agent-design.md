# Faza 3 — agentski AI sloj (chat koji ODRADI) — dizajn (2026-08-08)

Korisnikove odluke (dogovorene):
1. `pretraži` = lokalno (RAG) + internet (postojeći websearch). Sve
   ostalo (dodavanje, izmjena, označavanje, rok, bilješka...) AI radi
   kad mu se u razgovoru kaže.
2. **Potvrda SVAKI PUT za SVE mutacije** (uključujući dodavanje).
   Čitanje/pretraga se ne potvrđuje (to je odgovaranje, ne akcija).
3. AI nasljeđuje ovlasti radnika koji pita (nikad više).
4. Slabi modeli bez pouzdanog tool-callinga → "samo savjetuje" način.

## Ključni obrazac: PRIJEDLOG → POTVRDA → IZVRŠENJE

LLM NIKAD ne izvršava mutaciju izravno. Tok:
1. Radnik u chatu traži akciju ("dodaj klijenta Pekara Mlinar, OIB ...").
2. LLM (tool-calling) vrati PRIJEDLOG poziva alata (ime + argumenti).
3. Backend: validira alat + argumente, provjeri smije li radnik to
   (ovlasti), spremi PENDING akciju server-side (keyed tokenom, vezan uz
   actor + istek 10 min) — NE vjeruje klijentu da vrati akciju.
4. Chat vrati ljudski sažetak + token: "Dodat ću klijenta Pekara Mlinar
   (OIB ...). Potvrdi?" + gumb Potvrdi/Odustani.
5. Radnik potvrdi → POST /chat/potvrdi {token} → backend izvrši TOČNO
   spremljenu akciju (ne ono što klijent pošalje) + audit + rezultat u
   chat.
Čitanje (pretraži/popis/stanje) izvršava se odmah unutar agentske petlje
(bez potvrde) i rezultat se vraća LLM-u da sroči odgovor.

## Alati (prvi krug)

Registar `atlas/rag/agent_tools.py` — svaki alat: ime, opis (za LLM),
JSON schema argumenata, `readonly: bool`, `min_role`, funkcija
`run(spine, cfg, actor, args) -> dict`. Alati zovu POSTOJEĆI business
sloj (ne dupliciraju logiku) i poštuju vidljivost/ovlasti actora.

Read-only (bez potvrde):
- `pretrazi(upit)` — RAG (pipeline.answer/retrieval) + web (websearch)
  kad lokalno ne nađe ili upit traži web; vraća sažetak + izvore.
- `popis_obveza(filter)`, `stanje_klijenta(naziv|oib)`.

Write (potvrda obavezna):
- `dodaj_klijenta(naziv, oib, ...)`, `uredi_klijenta(id|oib, polja)`
- `oznaci_obvezu(klijent, vrsta, stanje)`
- `zakazi_rok(klijent, vrsta, datum)`
- `zapisi_belesku(klijent, tekst)`

Argumenti se STRIKTNO validiraju (OIB format postoji u security.oib_valid;
datumi; postojanje klijenta) PRIJE pending spremanja — LLM ne smije
izmisliti nevažeći OIB pa da padne tek na izvršenju.

## LLM tool-calling (atlas/core/llm.py proširenje)

- `complete(..., tools=None)` → kad su tools zadani i provider ih
  podržava, prosljedi ih u API (anthropic `tools`/`tool_use`; openai-compat
  `tools`/`tool_calls`); vrati LLMResult prošriren s `tool_calls`
  (lista {name, args}) uz text.
- `supports_tools(provider) -> bool` / capability: anthropic (OAuth i
  ključ), openai-compat s poznatim providerima da; ollama — ovisi o
  modelu (probaj, na grešku degradiraj). Za "samo savjetuje" način:
  agentski put se ne aktivira, chat radi kao danas (pipeline.answer).
- Bez novih depsa; ručno sklapanje request/response JSON-a kao postojeći
  complete.

## Agentska petlja (atlas/rag/agent.py)

`run_agent(spine, cfg, query, actor, llm, max_steps=4) -> dict`:
- system prompt: opiši ulogu, alate, pravilo "za promjene predloži pa
  čekaj potvrdu", hrvatski, ovlasti.
- petlja: LLM → ako tool_call read-only: izvrši, dodaj rezultat, nastavi;
  ako write: validiraj+spremi pending, VRATI prijedlog (prekid petlje,
  čeka potvrdu); ako nema tool_calla: vrati tekst kao finalni odgovor.
- max_steps zaštita (beskonačna petlja / model koji se vrti).
- greška alata → poruka LLM-u, ne pad.

## API + UI

- `/chat` (postojeći): kad je model tool-capable I actor smije akcije →
  run_agent; inače postojeći pipeline.answer. Odgovor prošriren:
  {text, sources, pending: {token, summary} | null}.
- Novi `POST /chat/potvrdi {token}` (require_actor_web): izvrši pending
  akciju (vlasništvo tokena = actor; istek; jednokratnost) + audit
  action="agent_execute" + vrati rezultat.
- `POST /chat/odustani {token}` — obriši pending.
- Pending store: tablica `agent_pending` (token, actor_user_id, org_id,
  tool, args_json, created_at) — aditivna migracija; čišćenje isteklih.
  (Ne in-memory — serve može biti restartan; DB je već tu.)
- UI (templates_ui chat): kad odgovor ima pending → prikaži sažetak +
  Potvrdi/Odustani (bez alert; postojeći toast za rezultat). Minimalno,
  bez redizajna (korisnik radi izgled).

## Sigurnost

- Ovlasti: svaki write alat provjeri min_role i vidljivost klijenta za
  actora PRIJE pending i PONOVO pri izvršenju (actor se čita iz tokena
  vlasnika, ne iz requesta).
- Pending token: kriptografski slučajan (secrets), vezan uz actor,
  jednokratan, istek 10 min; tuđi token → 403/404.
- Impersonated_by (admin-kao-radnik, faza 2): agentske akcije bilježe i
  njega u audit.
- LLM izlaz je NEPOVJERLJIV: argumenti se validiraju kodom, nikad se ne
  izvršava proizvoljni SQL/kod iz modela; alat popis je fiksan
  (allowlist), nepoznat tool_name → odbij.
- Web pretraga poštuje postojeći egress allow/proxy.

## Testabilnost

- agent_tools: svaki alat unit (mock spine, actor ovlasti, validacija
  odbija loš OIB/nepostojećeg klijenta); readonly izvršenje.
- llm tools: mock transport vraća tool_use/tool_calls → complete ih
  parsira; supports_tools po provideru.
- run_agent: lažni llm (skriptirani tool_calls) — read-only lanac,
  write→pending prekid, max_steps, greška alata; bez mreže.
- API: /chat s tool-capable mock llm → pending u odgovoru; /chat/potvrdi
  izvrši+audit; tuđi/istekli token odbijen; ovlasti (restringiran radnik
  ne smije tuđeg klijenta ni preko agenta).
- capability fallback: ne-capable model → stari pipeline put.

## Ne-ciljevi (kasnije faze / backlog)

- Alati koji diraju uređaje/struju/programe (faze 4-5).
- Chat streaming + klikabilni citati (B6, uz korisnikov UI).
- Višekoračne kompozitne akcije s jednom potvrdom (za sada 1 akcija =
  1 potvrda; batch "za sve obrte" = jedan alat s filterom + jedna
  potvrda koja u sažetku navede koliko klijenata dira).
