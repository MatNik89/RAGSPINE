# Prompt za sljedeću sesiju — security audit + polish + priprema za deploy

Kopiraj sve ispod crte u novu sesiju.

---

Nastavi RAGSPINE. Repo ~/Desktop/RAGSPINE/CLAUDE/v2, GitHub MatNik89/RAGSPINE,
grana master. Dozvoli Bash+Write. Cijeli program C1→G je gotov i CI je zelen na
3 OS-a (zadnji commit d65b840). Sad je cilj **sigurnosno ojačati i uglancati
RAGSPINE i pripremiti ga za prvo postavljanje na terenu (ured, LAN) i testiranje**.

Ovo je MOJ repo, obrambeni security rad na vlastitom kodu — audit i hardening,
nikakva vanjska meta. RAGSPINE je lokalni RAG za knjigovodstveni ured (FastAPI +
SQLite, LAN, per-firma install, LLM kroz claude CLI).

## 1. Napadni RAGSPINE (offensive audit vlastitog koda)

Ponašaj se kao napadač na vlastiti sustav. Pokrij barem:
- **Auth/authz**: JWT (falsifikat, istek, alg-confusion, cookie vs Bearer),
  org-izolacija, client_visibility (može li radnik do tuđih klijenata kroz BILO
  koji endpoint — dokumenti, print, karton, chat, assist).
- **Path/SSRF**: svi konzumenti nas_folder / KLIJENTI puta, OCR scope,
  devices LAN guard (rebinding, redirect), safe_fetch egress, arhitektura apply.
- **Injection**: SQL (parametri svugdje?), prompt-injection (dokument→LLM u C3
  ekstrakciji, assist sidebaru, chat pipelineu), XSS u template JS (svi kroz
  textContent?), xlsx/CSV formula injection, header injection.
- **DoS/resurs**: rate-limiti, veličine uploada/skena/keyworda, LLM trošak po
  requestu, regex ReDoS, mDNS/eSCL flooding.
- **Data-at-rest**: GDPR forget potpunost (doc_extracts, transkripti, scans),
  redakcija u exportu, 0600 dozvole, secret handling.
- **Uredski specifično**: e-račun autosort spoofing (lažni OIB), watchlist
  override trovanje (prirez/quickref preko zlonamjernog izvora).

Radi to kao **paralelni multi-agent red-team** (dimenzija → nalaz → adversarijalna
verifikacija u ≥2 nezavisna glasa prije nego se nalaz prihvati). Foldaj samo
POTVRĐENE nalaze, svaki s testom-regresijom. Lažno-pozitivne odbaci glasno.

## 2. Alati — istraži i iskoristi

- **nuclei v3.11 je instaliran** (/usr/bin/nuclei) — pravi DAST scanner. Podigni
  RAGSPINE lokalno (`ragspine serve` na loopbacku) i pusti nuclei s auth
  headerom protiv njega (exposures, misconfig, generic-detect templatei). N=
  provjeri i vlastite Python DAST/SAST: bandit, semgrep (instaliraj ako fali,
  MIT/Apache).
- **herdr v0.7.5** (~/.local/bin/herdr) NIJE security alat — to je **workspace
  manager za AI coding agente** (paralelne panele/agente/worktree, tmux-slično).
  Iskoristi ga za orkestraciju paralelnih audit-agenata i za čist workflow
  (svaki agent svoj worktree, bez gaženja). `herdr --help`, `herdr worktree`,
  `herdr agent` — prvo istraži subkomande pa uklopi u red-team.
- **Istraži GitHub repoe** za security testiranje FastAPI/Python weba i lokalnih
  LAN servisa (npr. nuclei-templates, semgrep-rules, FastAPI security checklists,
  eSCL/IPP sigurnosne poznate rupe). Za svaki: je li primjenjiv, licenca, što
  točno posuditi (ideja > kopiranje koda; AGPL-denylist).

## 3. Polish + priprema za deploy

- **doctor**: `ragspine doctor` mora iskreno pokriti sve nove dijelove
  (uređaji, arhitektura, doc-types, ekstrakcija) — što fali za produkciju.
- **Getting started / deploy**: korak-po-korak za prvi ured (install, secret,
  operator model, KLIJENTI mapa registracija, dogovor strukture, uređaji,
  ključne riječi) — provjeri postoji li i je li točan.
- **Smoke na stvarnom CLI-ju** (dogfood): podigni, prođi kroz login→dodaj
  klijenta→skeniraj (fake)→ekstrakcija→praćenje→export, uhvati što unit-testovi
  promaše.

## Pravila (kao dosad)

Svaki fix: measure-first + test-regresija + Codex adversarial review
(`codex exec --sandbox read-only -c model_reasoning_effort=medium`, foldaj ili
obrazloži skip) + `git push` + `gh run watch` CI zelen 3 OS. Cyrillic-gate
(tests/test_no_cyrillic.py) mora ostati zelen. Ažuriraj memoriju na kraju.

Kontekst i povijest programa: memorija [[ragspine-c1-ocr-done]] +
[[ragspine-arhitektura-dogovor]]; specovi u docs/ (C2/C3/D/E/F/G).
Parkirano/ne-dirati bez dogovora: PLAĆE most, veći vizualni polish (dogovara se
s korisnikom), IPv6 discovery.
