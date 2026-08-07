# TUI face-lift 3 — dizajn (2026-08-07)

## Cilj

Ostatak dorada iz next-tui-grana.md bez cert bootstrapa: prečac na radnoj
površini (sve platforme), pull s kvant sufiksom + usporedba veličine,
kozmetika (kose crte, fitz/fastembed warnings, bge-m3 feature-detect).

## Opseg

1. **Prečac na radnoj površini (E2E: korisnik zatvorio app-prozor i nije se
   znao vratiti)** — novi modul `atlas/ops/shortcut.py`:
   - `create_desktop_shortcut(url, *, name="ATLAS", out=print, ...) -> bool`
   - Windows: `.lnk` preko PowerShell WScript.Shell (Target = pronađeni
     msedge/chrome iz poznatih lokacija, Arguments `--app=<url>`; app-prozor
     bez browser UI-ja) + kopija u Start Menu Programs; kad browser nije
     nađen → `.url` datoteka (zadani preglednik) kao fallback
   - Linux: `~/Desktop/ATLAS.desktop` (`Type=Application`,
     `Exec=xdg-open <url>`, chmod +x); poštuj XDG_DESKTOP_DIR ako postoji
   - macOS: `~/Desktop/ATLAS.webloc` (plist XML)
   - Desktop mapa ne postoji → poruka, bez pada; svi subprocess pozivi
     injektabilni (testovi bez pravih procesa)
   - Integracija: `wizard.launch_now` — prečac se stvara AUTOMATSKI (bez
     pitanja) čim je URL poznat, neovisno o odluci o startu servera;
     ispiše se rezultat ("✓ Prečac: ..." / "⚠ ...")
2. **Pull s kvant sufiksom (E2E BUG: obećani i stvarni footprint se
   razilaze)**:
   - `preflight.quant_tags(ollama_name, quant) -> list[str]` — kandidati
     `<ime>-instruct-<q>` pa `<ime>-<q>` (q = "Q4_K_M" → "q4_K_M");
     prazno kad nema kvanta ili ime nema ":"
   - `page_model`: pull redom kandidat-tagovi pa goli tag; uz fallback na
     goli tag ⚠ "registry nema izračunati kvant — zadani tag može biti
     veći od procjene"; SPREMA SE stvarno skinuti tag (model_settings)
   - `preflight.ollama_model_size(tag, url) -> float` (GB, 0.0 na grešku)
     preko GET /api/tags; nakon pulla ispiši stvarnu veličinu uz procjenu
     (model_table.disk_gb) i ⚠ kad je stvarna > 1.3 × procjena
3. **Kozmetika**:
   - miješane kose crte (`C:\Users\X/.atlas`): `config.from_env` —
     `os.path.normpath` na data_dir (pokriva i env vrijednosti s `~/...`)
   - fitz DeprecationWarning curi u stranicu 1: `preflight.requirements`
     optional-modul petlja pod `warnings.catch_warnings()` +
     `simplefilter("ignore")`
   - fastembed mean-pooling + HF symlink upozorenja u `--download-models`:
     `embed.download_model` postavi `HF_HUB_DISABLE_SYMLINKS_WARNING=1` i
     priguši warnings oko downloada
   - bge-m3 u ponudi, a fastembed ga ne podržava: novi
     `embed.supports(model_name) -> bool` (fastembed
     `TextEmbedding.list_supported_models()`, robusno na import/API
     greške → False); `wizard.choose_embed_model` nudi bge-m3 SAMO kad
     `embed.supports(_BGE_M3)`

## Provjereno — ispada iz opsega

- MODEL_CATALOG: ne postoji u kodu (grep prazan) — stavka iz nalaza već
  riješena ranijim redizajnom (llmfit jedini izvor).
- getpass za `atlas auth add`: već koristi getpass (atlas/__main__.py:89).
- Cert bootstrap + prijateljsko ime: zasebna grana (korisnikova odluka).

## Testabilnost

- shortcut: injektabilan `run` (subprocess), tmp Desktop kroz monkeypatch
  HOME/USERPROFILE/XDG; sadržaj .desktop/.webloc/.url se asertira; .lnk
  grana asertira generirani PowerShell skript-string, ne izvršenje
- quant_tags/ollama_model_size: čisto + mockan urlopen
- page_model pull petlja: mockan preflight.ollama_pull sekvencama
  False/True; spremljeni tag se čita iz spinea
- kozmetika: normpath unit; requirements bez warning curenja (recwarn/
  capsys); embed.supports s mockanim fastembed modulom

## Rizici

- WScript.Shell CreateShortcut treba puni put do browsera — poznate
  lokacije za msedge/chrome (Program Files varijante); nijedan nađen →
  .url fallback (radi uvijek, samo bez app-prozora).
- Registry tag konvencije variraju po modelu — zato lista kandidata s
  golim tagom kao zadnjim; pogrešan kandidat samo ispiše grešku pulla i
  ide dalje.
- list_supported_models API se mijenjao kroz fastembed verzije — supports()
  hvata svaku iznimku i vraća False (sigurni default = mali model).
