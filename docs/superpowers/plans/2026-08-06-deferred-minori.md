# Plan: deferred minori (setup wizard)

Grana: `chore/deferred-minori`. Odgođene sitnice iz P1-P4 reviewa setup wizarda.

## Global Constraints

- Hrvatski (latinica s dijakriticima) u komentarima, porukama i commit porukama.
- Bez novih ovisnosti.
- Testovi bez mreže, bez stdina, bez pravih subprocessa (popen se injektira, subprocess se monkeypatcha).
- Puni test suite se vrti U PRVOM PLANU (foreground), ne u backgroundu.
- Commit poruke: hrvatske konvencionalne (`chore(...)`, `test(...)`) s footerom
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Task 1: ne-UNC guard za SMB hint + komentari + PEP8

Datoteke: `ragspine/ops/wizard.py`, `ragspine/ops/preflight.py`, `tests/conftest.py`, `tests/test_wizard.py`.

1. **`ragspine/ops/wizard.py`, `page_mape`** — na grani `not os.path.isdir(path)` sada se
   BEZUVJETNO ispisuje `net use` hint (`_net_use_hint(path)`), što je besmisleno kad
   korisnik utipka lokalnu putanju s tipfelerom (npr. `C:\klijenti` ili `/mnt/typo`).
   Guard: hint ispiši samo ako `path.startswith("\\\\")`; inače ispiši redak:
   `    Putanja nije UNC (\\server\share\...) — provjeri tipfeler.`
   (`_net_use_hint` sam ostaje netaknut — poziva se samo iza guarda.)
2. **`ragspine/ops/wizard.py`, `page_mape`** — uz postojeće upozorenje o slovu pogona
   (`Slovo pogona (npr. Z:)`) dodaj `ponytail:` komentar: upozorenje ne blokira jer
   `os.path.realpath` na Windowsu mapped-drive obično razriješi u UNC; blokada bi
   lažno odbila valjane konfiguracije.
3. **`ragspine/ops/preflight.py`, komentar iznad `_llmfit_cache`** — dopuni postojeći
   komentar rečenicom: uspješan-ali-prazan rezultat (llmfit radi, 0 modela prođe
   filter) kešira se zauvijek — bezopasno, jer retry grana u wizardu gleda PATH
   (dostupnost llmfita), ne sadržaj liste.
4. **`tests/conftest.py`** — PEP8: dva prazna reda prije i poslije fixture
   `_reset_llmfit_cache` (sada je jedan prazan red iza nje, prije `cfg`).
5. **Test** u `tests/test_wizard.py`: ne-UNC nepostojeća putanja u `page_mape`
   (npr. `/nema/takve/mape`) — očekuj redak s `nije UNC` i NIJEDAN redak s `net use`.
   Postojeći test `test_net_use_hint_iz_unc_putanje` i UNC grana moraju i dalje raditi
   (UNC nepostojeća putanja i dalje dobije `net use` hint — po potrebi dodaj i taj assert).

Provjera: `python -m pytest tests/test_wizard.py -q` pa puni suite u prvom planu.

## Task 2: testovi — Ollama backup putanja obje OS grane + OSError na Edge popen

Datoteka: `tests/test_wizard.py`.

1. **`page_gotovo` Ollama backup putanja, obje OS grane** — postojeći
   `test_page_gotovo_backup_da` asserta samo substring `.ollama` pa prolazi u objema
   granama. Dodaj test (ili dva) koji monkeypatchaju `wizard.os.name`:
   - `monkeypatch.setattr(wizard.os, "name", "nt")` → izlaz sadrži
     `%USERPROFILE%\.ollama\models`
   - `monkeypatch.setattr(wizard.os, "name", "posix")` → izlaz sadrži
     `~/.ollama/models`
   Backup pitanje odgovori "d"/"" uz monkeypatchani `wizard.backup.create_backup`
   (kao u postojećim testovima) ili odbij s "n" — bitna je samo putanja u izlazu.
2. **`launch_now` OSError na Edge grani** — postojeći `test_launch_now_oserror_ne_rusi`
   pokriva pad PRVOG popena (serve). Dodaj test: `platform.system` → "Windows",
   injektirani `popen` koji prvi poziv (serve) prođe, a drugi (Edge) digne
   `OSError`; očekuj da ništa ne propagira, izlaz sadrži `Server pokrenut` i
   `Otvori ručno` s URL-om.

Provjera: `python -m pytest tests/test_wizard.py -q` pa puni suite u prvom planu.
