# Model isporuke + status Codex full-branch nalaza

**Datum:** 2026-08-03
**Odluka vlasnika:** RAGSPINE se isporučuje **jedan install po firmi** — svaka
knjigovodstvena firma ima vlastitu instalaciju i vlastitu bazu. NE postoji jedan
zajednički sustav koji poslužuje više firmi.

**Posljedica:** cross-org (multi-tenant) izolacija **nije bezbjednosna tema** —
u svakoj bazi je uvijek samo jedna firma, pa nema tuđih podataka za procuriti.
TIER 0/1 org-strojerija (orgs/memberships/ACL) ostaje izgrađena i bezopasna;
ACL rang se prenamjenjuje za **intra-uredsku vidljivost klijenata po radniku**.

## Codex full-branch review (commit boyy8y948 dump) — status svakog nalaza

Codex je reviewao pod pretpostavkom "jedan sustav, više firmi" (multi-tenant SaaS).
Uz stvarni model isporuke većina nalaza je **inertna po dizajnu**:

| # | Nalaz (Codex) | Status |
|---|---|---|
| 1 | legacy tablice (clients/notes/obligations…) bez org_id, globalne | INERTNO (jedna firma po bazi). Intra-uredsku vidljivost klijenata rješava nova funkcija. |
| 2 | /knowledge status/supersede/promote username-only, mutate-by-id | INERTNO za cross-org. Ostaje intra-uredski: svaki radnik smije upravljati dokumentima ureda (politika "svi rade sve" osim vidljivosti klijenata). |
| 3 | graf/SQL lane + client_context globalni | DJELOMIČNO RIJEŠENO: client_context.resolve_client sad poštuje vidljivost radnika. Graf/SQL lane ostaju uredski-globalni (nema tuđe firme). |
| 4 | ingest bez actor org → default org | INERTNO (jedan org "Ured" po bazi). |
| 5 | login bootstrap zalijepi u default org | INERTNO (jedan org po bazi). |
| 6 | wiki visibility/ACL polja se ne provode | OTVORENO (nisko): unutar ureda "private" wiki curi svim članovima. Backlog — politika ureda je ionako "svi vide sve" pa niska šteta. |
| 7 | /model preusmjeravanje = exfiltracija | **RIJEŠENO** (commit 1cc789d): POST /model admin-only. |
| 8 | interactions/memory/peer bez org_id | INERTNO (jedna firma). |
| 9 | /audit scope po članstvu, ne po org eventa | INERTNO (jedna firma; svi akteri su iz ovog ureda). |
| 10 | forget/export bez org filtra | INERTNO za cross-org. Otvoreno (srednje): forget() ne briše mem_l0/l1/l3/wiki/skills/sop — GDPR potpunost, backlog. |
| 11 | dedup global (sha256 unique) | INERTNO (jedna firma; nema tuđeg sadržaja za oracle). |

## Zaključak
Grana je **sigurna kao jedno-uredski alat**. Otvoreni backlog (nezavisan od
modela isporuke): wiki visibility enforcement (#6), forget() potpunost brisanja
novih memory/wiki/skills tablica (#10). Ostali "ranije-postojeći endpointi bez
org_id" nisu bugovi u ovom modelu isporuke.
