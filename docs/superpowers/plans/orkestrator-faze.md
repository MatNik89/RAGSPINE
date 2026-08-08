# ATLAS orkestrator ureda — fazni plan (dogovoren s korisnikom 2026-08-08)

Vizija: Atlas server = mozak ureda (baza + AI + orkestracija), radnici =
browser + tanki agent, UPS = signal za struju. AI u chatu ODRAĐUJE
(alati nad bazom i uredom), ne samo odgovara.

Korisnikove odluke (standing):
- Provideri: Claude + OpenAI ISKLJUČIVO OAuth (pretplate, tokeni na
  serveru); DeepSeek + Kimi API ključem; Ollama lokalno. B12 (http za
  lokalne servere) NE treba.
- B13 (mrtve rute /dashboard, /obveze.json) brisati TEK kad korisnik
  definira konačni UI.
- Login redizajn + paleta + izgled: KORISNIK radi na drugom računalu
  (mockupi na Nicku); mi radimo isključivo backend.
- Univerzalna šifra: NEMA zasebne — radnikov user + ADMIN lozinka =
  ulaz kao taj radnik, audit bilježi "admin ušao kao <user>".

## Faza 1 — WinSW/systemd servis (24/7 temelj)
Pravi servis: preživi zatvaranje terminala, odjavu, restart; sam se
digne s bootom (uz BIOS "Restore on AC" na serveru — jednokratna ručna
BIOS postavka, Atlas ispiše uputu). Restart servisa ubija CIJELO stablo
procesa (E2E nalaz: python zombi servira stari kod). Log u datoteke
(.atlas/serve.out.log / serve.err.log), ne DEVNULL. winsvc.py postoji
kao začetak — proširiti. Rješava i E2E KRITIČNO: detached serve umire
sa zatvaranjem terminala.

## Faza 2 — Radnici + uređaji + aktivacijski login
- Postavke → Radnici: admin dodaje radnike (user, bez šifre — stanje
  "čeka aktivaciju"); gumb "Resetiraj šifru".
- Postavke → Uređaji (proširenje postojećeg): admin dodaje radne
  stanice/NAS/printere; SPOSOBNOSTI po uređaju (kvačice: gasi pri
  nestanku struje + redni broj; probudi na struju/WOL; smije pokretati
  programe; samo nadzor); VEZANJE uređaj ↔ radnik (padajući izbornik).
  Mrežno skeniranje samo kao pomoć pri dodavanju (prijedlozi).
- Aktivacijski login: radnik prvi put upiše samo usera → "Dobrodošao,
  <ime> — postavi svoju šifru" (2 polja, min 8, moraju se poklopiti) →
  aktiviran.
- Admin-kao-radnik: user radnika + admin lozinka = ulaz kao radnik;
  audit: "admin ušao kao <user>".

## Faza 3 — Agentski AI sloj (chat koji ODRADI)
Tool-calling preko postojećeg LLM dispatchera: popis alata nad bazom
(dodaj_klijenta, označi_obvezu, zakaži_rok, zapiši_bilješku,
pretraži...). Pravila: AI nasljeđuje ovlaštenja radnika koji pita
(nikad više od njega); destruktivne akcije traže potvrdu u chatu;
SVAKI AI potez u audit log. Radi sa svim providerima (OAuth/ključ/
Ollama).

## Faza 4 — UPS/NUT + gašenje redom
Postavke → Napajanje: NUT (Network UPS Tools; apcupsd za APC) adresa,
pragovi ("na bateriji > X min → gasi"). Događaji: na bateriji / niska
baterija / struja se vratila. Gašenje po rednom broju iz Postavke →
Uređaji (radnici prvo, NAS, server zadnji). Izvršenje: SSH za NAS/
servere, agent za radnička računala (faza 5; do tada SSH gdje ga ima).

## Faza 5 — Radnički agent + WOL + pokretanje programa
- `atlas-agent`: mala Python skripta (isti repo), vrti se u radnikovoj
  SESIJI (Task Scheduler pri prijavi), spaja se VAN prema serveru
  (wss na 8443) — bez portova na radničkom stroju. Token po uređaju
  (izdaje se u Postavke → Uređaji; opoziv = uređaj gluh).
- Tvrda allowlista radnji U AGENTU: pokreni program (samo s admin
  liste programa), uredno ugasi, javi status, uključi WOL postavku
  mrežne kartice. Sve ostalo odbija bez obzira što server pošalje.
- Instalacija kao cert: bootstrap stranica /postavi-agent (uv install
  + token + Task Scheduler registracija).
- WOL: server pri dizanju budi radnička računala magic paketom,
  redoslijedno; agent prethodno uključio "wake on magic packet" u OS-u.
- Chat: "kod Ane otvori X" → veza radnik↔uređaj (faza 2) → agent.

## Poslije (postojeći backlog, ne dio ovih faza)
Chat streaming + citati payload (B6, uz korisnikov novi UI); DPAPI;
tagirane verzije + atlas upgrade; čista E2E proba (briše se Nick).
