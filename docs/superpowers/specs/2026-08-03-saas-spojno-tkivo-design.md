# SaaS spojno tkivo — org-kontekst kroz cijeli sustav — dizajn

**Datum:** 2026-08-03
**Cilj:** TIER 0/1 substrat (orgs/ACL/wiki/skills/memorija) je izgrađen ali NIJE spojen na
aplikaciju. Ova runda ga ožičuje: auth nosi org-kontekst, retrieval i cache su org-scoped,
wiki/skills/memorija ulaze u chat, Postavke dobivaju ekrane Organizacija/Wiki/Skills.

## Faza A — org-kontekst u auth
- `tenancy.default_org_id(spine)` — MIN(id) org; ako nema nijedne, kreira "Ured"
  (bootstrap jedno-uredske instalacije).
- `tenancy.resolve_login_org(spine, user_id, sys_role)` — prvo postojeće članstvo;
  inače članstvo u default org: prazan org → `owner`, sys_role admin → `admin`, inače `member`.
- Login: JWT claims prošireni s `uid` + `org_id` (pokazivači, NE uloga — uloga se čita
  svježa iz memberships na svakom zahtjevu = trenutna revokacija).
- `deps.require_actor` / `require_actor_web` — vraćaju `Actor` (user_id, org_id, role,
  team_ids, username). Stari token bez claimova → fallback lookup po username (24h prijelaz).
- `Actor.username` polje (za audit).
- `GET /org` — info + članovi (svaki član smije čitati).

## Faza B — org-scope podataka i retrievala
- `documents.org_id`, `knowledge.org_id` kolone (aditivna migracija) + backfill na
  default org pri create_app.
- `ingest_text(..., org_id=None)` — None → default org (svaki insert je uvijek stampan).
- `retrieval.search(..., org_id=None)` — org_id zadan → tvrdi filtar `d.org_id=?`.
- `cache.get/put(..., org_id)` — qhash uključuje org (cross-org cache leak zatvoren).
- `kb.lookup/save(..., org_id)` — org filtar.
- `pipeline.answer(..., actor=None)` — API uvijek šalje Actor; bez actora (CLI/testovi)
  ponašanje ostaje globalno (back-compat).
- Ostale poslovne tablice (clients/obveze/...) NAMJERNO ostaju bez org_id ovaj krug —
  retrieval/znanje je sigurnosno-kritični put; puni org-scope poslovnih tablica = zaseban krug.

## Faza C — znanje u chatu
- Chat lane s actorom dodaje "extra" blok u composer (podaci-ne-naredbe okvir već postoji):
  L3 persona + L1 atomi (`memory_layers.recall`), top skill (`skills.match`), top-2 wiki
  (`wiki.search`). Sve best-effort — nikad ne ruši odgovor.
- Nakon odgovora `record_turn` (L0) za user+assistant.
- Dnevni job `memory_distill` (03h): distill + build_persona za sve (org,user) s
  nedestiliranim L0 zapisima; bez LLM-a tiho preskače.

## Faza D — endpointi + ekrani
- `/org/members` POST (admin+), `/org/members/{uid}/role` POST (admin+; zadnji owner
  se ne smije degradirati).
- `/wiki` GET, `/wiki/search` GET, `/wiki/{slug}/lock` POST (admin+).
- `/skills` GET/POST, `/skills/{id}` POST, `/skills/{id}/status` POST.
- Ekrani `/ui/org`, `/ui/wiki`, `/ui/skills` + kartice u Postavkama. Postojeći stil:
  page_shell, textContent, script_json, credentials same-origin.

## Sigurnost/invarijante
- Uloga NIKAD iz tokena — uvijek svježa iz memberships (revokacija radi odmah).
- Tvrdi org filtar u retrieval/wiki/skills/memoriji; cache/kb org-keyed.
- Admin gate za mutacije org/wiki-lock; zadnji owner nedegradabilan.
- Extra-kontekst u promptu je unutar postojećeg anti-injection okvira.
