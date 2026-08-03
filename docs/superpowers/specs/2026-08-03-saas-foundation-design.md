# SaaS temelj — multi-tenancy + RBAC + ACL (TIER 0, Faza 1) — dizajn

**Datum:** 2026-08-03
**Cilj:** pretvoriti RAGSPINE iz jedno-uredskog alata u multi-tenant platformu prodajivu firmama od 100–1000 ljudi. Faza 1 = substrat: izolacija po organizaciji, uloge (RBAC), vidljivost + ACL po sredstvu. Kasnije faze (wiki, skills, dijeljena memorija) sjede na ovome.

**Ukradeno (reimplementirano čisto):** Tencentov ordered permission-checker + "empty-first fast path" (ACL tablica se čita tek na promašaj). Njihov ACL-model je dobar; auth im je slab (bearer u localStorage) — mi zadržavamo JWT + httpOnly cookie.

## Podatkovni model (nove tablice)
- `orgs(id, name, created_at)` — tenant.
- `memberships(id, org_id, user_id, role, UNIQUE(org_id,user_id))` — role ∈ owner|admin|member|viewer.
- `teams(id, org_id, name)` + `team_members(team_id, user_id, PK(team_id,user_id))`.
- `asset_acl(id, asset_type, asset_id, subject_type, subject_id, permission, UNIQUE(...))` — subject_type ∈ user|team|role; permission ∈ read|write|manage|delete.

## Model dozvola
- **Uloge (rang):** viewer<member<admin<owner.
- **Vidljivost sredstva:** private | team | org | restricted.
- **Akcije:** read | write | manage | delete.

`check(actor, asset, action, acl_rows=None) -> bool` (čista funkcija, redoslijed):
1. `actor.org_id != asset.org_id` → **deny** (tvrda tenant-izolacija; nikad cross-org).
2. `actor == asset.owner` → allow (sve).
3. `actor.role ∈ {admin, owner}` → allow (upravlja svime u svojoj org).
4. read: visibility `org` → svi članovi; `team` → isti tim; `private` → samo owner; `restricted` → samo ACL.
5. write/delete/manage (ne-owner, ne-admin) → samo preko podudarnog ACL retka.
6. default **deny**.

`can(spine, actor, asset, action)`: prvo `check(...,acl_rows=None)` (fast path); ACL iz baze se učita **samo** ako je fast path deny i (visibility restricted ili akcija write/delete/manage).

## Komponente
- `ragspine/business/acl.py` — `Actor`, `Asset` dataklase, `ROLE_RANK`, `check()` (pure), `can()` (DB), `_acl_matches`, `grant()/revoke()/load_acl()`.
- `ragspine/business/tenancy.py` — `create_org`, `add_member`, `role_of`, `actor_for(spine,org_id,user_id)` (učita role+team_ids), `create_team`, `add_to_team`, `list_members`.
- `ragspine/core/spine.py` — nove tablice.

## Sigurnost/invarijante
- Tenant-izolacija je prvo pravilo i tvrda (cross-org uvijek deny).
- Owner uvijek zadržava pristup; admin/owner org-scope.
- ACL je allow-only (nema deny-override) — jednostavnije, manje rupa.
- Sve parametrizirano; `can()` lazy-load minimizira upite.

## Testiranje
- tenant-izolacija (drugi org → deny za sve akcije);
- owner allow; admin allow u org; viewer read-only po vidljivosti;
- private/team/org/restricted matrica;
- ACL grant (user/team/role) omogući write; revoke; empty-first fast path ne dira ACL kad nije nužno;
- role_of/actor_for/create_org(owner membership).

## Faze (poslije 1)
2. Ožičiti postojeća sredstva (documents/sop/notes/folders/obligations) da nose org_id+owner+visibility; scope retrieval/pipeline po org+ACL.
3. TIER 1: LLM-Wiki, L0→L3 memorija (per org/user), Skills.
4. PostgreSQL put + async job queue + audit/observability + rate-limit.
