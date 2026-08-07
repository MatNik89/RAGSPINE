# Wizard bez stranice mapa — dizajn (2026-08-07)

## Odluka (korisnikova)

Odabir mrežnih mapa IZLAZI iz TUI setup wizarda — sve ide kroz web
Postavke → Mrežne mape. Wizard = minimalni put "od nule do prijavljenog
admina u browseru" (5 stranica): preduvjeti → operater → model →
mreža/HTTPS → gotovo. Sve što web UI može sam, ne ide u wizard.

## Opseg

1. **wizard.py**:
   - `run()`: izbaci page_mape korak; stage lanac postaje 1-5
     (gotovo = stage 5). RESUME KOMPATIBILNOST: stara baza sa stage=4
     (prošla mrežu) ili stage=5 (prošla stare mape) nastavlja na
     "gotovo" — uvjet `if stage < 5` to pokriva za obje.
   - Obriši: `page_mape`, `_MAPE_ULOGE`, `_net_use_hint`, `_drive_warn`,
     import `folder_picker`.
   - Renumeriraj naslove: 1/5 ... 5/5 (i završnu poruku "Setup dovršen
     (5/5)").
   - `render_summary`: redak mapa kad ih nema → "Mape: — dodaj nakon
     prijave (Postavke → Mrežne mape)". Kad postoje (upgrade slučaj),
     ispis ostaje.
   - `launch_now` mount-roots logika OSTAJE (čita folders tablicu —
     upgrade instalacije mogu imati mape).
2. **Brisanje**: `atlas/ops/folder_picker.py`, `tests/test_folder_picker.py`
   (mrtav kod — jedini potrošač bila stranica 5).
3. **Web UI**: dashboard "Orijentacija — spojene mape" prazno stanje već
   kaže "Spoji mapu u Postavke → Mrežne mape." — dodaj klikabilan link
   na /ui/mape (jedina web izmjena; poruka postaje i primarni put do
   mapa nakon skraćenog wizarda).
4. **Testovi**: obriši page_mape testove; run()/flow testove uskladi
   (kraći input nizovi); page_gotovo/naslovi na novu numeraciju; novi
   test resume kompatibilnosti (stage=5 stara baza → ide na gotovo, ne
   pada); dashboard link test ako postoji obrazac za JS asercije
   (inače string-assert u templateu).

## Ne-ciljevi

- folders backend, /ui/mape, UNC provjere u webu — netaknuti.
- Dokumentacija F_WIZARD/DEPLOY_URED — usput ažurirati brojeve stranica
  ako spominju 6 stranica (grep).
