"""Terminal setup wizard. Jedan fiksni slijed, resume preko wizard_state.
P2: Stranice 1 (preduvjeti) + 2 (operater) + 3 (model). Ostale stranice u P3-P4."""
import dataclasses
import re
import time

from ragspine.core.llm import LLMError, LLMUnavailable
from ragspine.ops import preflight, tui, wizard_state
from ragspine.web import firstrun

_MIN_PW = 8
_BGE_M3 = "BAAI/bge-m3"
_BGE_M3_GB = 1.2   # fp16, približno (ručno kurirano)


def render_preflight(reqs, *, out=print) -> bool:
    """Ispiši preduvjete s glyph+detalj; fix samo za warn/fail. Vrati True kad nema 'fail'."""
    has_fail = False
    for r in reqs:
        g = tui.status_glyph(r["status"])
        out(f"  {g} {r['naziv']} — {r['detalj']}")
        if r["status"] in ("warn", "fail") and r.get("fix"):
            out(f"      → {r['fix']}")
        if r["status"] == "fail":
            has_fail = True
    return not has_fail


def page_preduvjeti(spine, cfg, *, input_fn=input, out=print) -> bool:
    tui.print_header("1/6  Preduvjeti", out=out)
    while True:
        reqs = preflight.requirements(cfg)
        if render_preflight(reqs, out=out):
            return True
        out("")
        out("Neki obavezni preduvjeti nedostaju (✗). Popravi ih pa ponovi.")
        if not tui.prompt_yes_no("Provjeri ponovno?", default=True, input_fn=input_fn, out=out):
            return False


def page_operater(spine, *, input_fn=input, out=print) -> bool:
    """Stranica 2: kreira prvog admina (operatera). Vrati True na uspjeh.
    Ako admin već postoji (npr. kreiran preko web /setup/owner puta, ili je
    prošli pokušaj pao između create_first_owner i set_stage), preskoči
    prompt — inače resume nema izlaza (create_first_owner uvijek baca)."""
    if not firstrun.needs_onboarding(spine):
        out("Administrator već postoji — preskačem.")
        return True
    tui.print_header("2/6  Operater (administrator)", out=out)
    while True:
        username = tui.prompt_text("Korisničko ime", input_fn=input_fn, out=out)
        if username:
            break
        out("Korisničko ime ne smije biti prazno.")
    while True:
        pw = tui.prompt_text("Lozinka (min 8)", input_fn=input_fn, out=out)
        if len(pw) < _MIN_PW:
            out(f"Lozinka mora imati barem {_MIN_PW} znakova.")
            continue
        pw2 = tui.prompt_text("Ponovi lozinku", input_fn=input_fn, out=out)
        if pw != pw2:
            out("Lozinke se ne podudaraju.")
            continue
        break
    try:
        firstrun.create_first_owner(spine, username, pw)
    except ValueError as e:
        out(f"Greška: {e}")
        return False
    out(f"Administrator '{username}' kreiran.")
    return True


def _download_embed(cfg):
    """Indirekcija radi testabilnosti (embed vuče fastembed tek pri pozivu)."""
    from ragspine.rag import embed
    return embed.download_model(cfg)


_SELF_TEST_PROMPT = "Odgovori točno: OK RAGSPINE"


def _llm_complete(spine, cfg, prompt: str):
    """Indirekcija radi testabilnosti; LLMClient ima vlastiti timeout (120 s)
    koji pokriva i cold-load većih modela."""
    from ragspine.business import model_settings
    from ragspine.core.llm import LLMClient
    return LLMClient(model_settings.apply(spine, cfg)).complete(
        [{"role": "user", "content": prompt}], max_tokens=20)


def self_test(spine, cfg, *, input_fn=input, out=print, retries: int = 3) -> bool:
    """Kratki test odabranog modela. Uspjeh = ne-prazan odgovor unutar timeouta.
    Regex "OK RAGSPINE" = soft-check (upozorenje). Kvar ne ruši setup."""
    for attempt in range(1, retries + 1):
        out(f"Self-test modela (pokušaj {attempt}/{retries}; prvi odziv zna trajati i minutu)...")
        t0 = time.monotonic()
        try:
            res = _llm_complete(spine, cfg, _SELF_TEST_PROMPT)
        except Exception as e:
            out(f"  ✗ {e}")
            if attempt < retries and tui.prompt_yes_no(
                    "Pokušaj ponovno?", default=True, input_fn=input_fn, out=out):
                continue
            return False
        text = (getattr(res, "text", "") or "").strip()
        if not text:
            out("  ✗ prazan odgovor")
            if attempt < retries and tui.prompt_yes_no(
                    "Pokušaj ponovno?", default=True, input_fn=input_fn, out=out):
                continue
            return False
        elapsed = time.monotonic() - t0
        out(f"  ✓ model odgovara ({elapsed:.1f} s)")
        if not re.search(r"OK RAGSPINE", text, re.IGNORECASE):
            out("  ⚠ upozorenje: odgovor ne sadrži 'OK RAGSPINE' — model radi, ali slabo slijedi upute.")
        return True
    return False


def choose_embed_model(state: dict, default_model: str) -> str:
    """bge-m3 kad KOMOTNO stane u ukupni RAM; inače ostavi default (mali)."""
    total = state.get("ram_total_gb") or 0.0
    if preflight.fit_pill(_BGE_M3_GB, total) == "fits":
        return _BGE_M3
    return default_model


def setup_embedding(spine, cfg, *, out=print) -> str | None:
    """Odaberi embedding po RAM-u, skini i VERIFICIRAJ; na grešku fallback na
    cfg.embed_model. Vrati ime verificiranog modela ili None (ne blokira setup)."""
    chosen = choose_embed_model(preflight.system_state(cfg), cfg.embed_model)
    for candidate in dict.fromkeys([chosen, cfg.embed_model]):   # bez duplikata
        out(f"Embedding model: {candidate} — skidam i provjeravam...")
        res = _download_embed(dataclasses.replace(cfg, embed_model=candidate))
        if res.get("ok"):
            out(f"  ✓ {candidate} (dim {res.get('dim')})")
            return candidate
        out(f"  ⚠ {candidate}: {res.get('error', 'nepoznata greška')}")
    out("Embedding nije skinut — RAG indeksiranje neće raditi dok se ne skine u Postavkama.")
    return None


_PILL_GLYPH = {"Good": "🟢", "Marginal": "🟡", "Too Tight": "🔴"}


def render_llmfit_models(rows, *, out=print) -> list[str]:
    """Ispiši llmfit retke (već filtrirane i sortirane po score-u); vrati
    ollama imena u prikazanom redoslijedu. Prvi = ⭐ preporuka."""
    names = []
    for i, r in enumerate(rows):
        pill = _PILL_GLYPH.get(r["fit_label"], "?")
        star = "  ⭐ PREPORUKA" if i == 0 else ""
        out(f"  {pill} {r['ollama_name']} ({r['params']}, {r['best_quant']} "
            f"~{r['memory_gb']:.1f} GB, ~{r['tps']:.0f} tok/s) — {r['use_case']}{star}")
        names.append(r["ollama_name"])
    return names


def page_model(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 3: Ollama spremnost -> llmfit lista -> JEDAN model -> pull -> spremi
    -> embedding -> self-test. Skip-grana vraća True (spec: ne zaglavi)."""
    tui.print_header("3/6  Model (LLM)", out=out)
    url = getattr(cfg, "ollama_url", "http://127.0.0.1:11434")

    ok, detail = preflight.ollama_ready(url)
    if not ok:
        out(f"Ollama: {detail} — pokušavam pokrenuti servis...")
        ok = preflight.start_ollama(url=url)
    if not ok:
        out("Ollama nije dostupna. Model možeš postaviti kasnije u Postavkama.")
        if tui.prompt_yes_no("Preskoči stranicu modela?", default=True,
                             input_fn=input_fn, out=out):
            return True
        return False

    ver = preflight.ollama_version(url)
    if not preflight.ollama_floor_ok(ver):
        out(f"⚠ Ollama verzija {ver or 'nepoznata'} < {preflight._OLLAMA_FLOOR} — preporučen upgrade "
            "(winget upgrade Ollama.Ollama). Nastavljam.")

    rows = preflight.llmfit_models(cfg)
    if not rows:
        out("llmfit nije dostupan ili nema modela koji stanu — model postavi kasnije u Postavkama.")
        return True
    out("Modeli za ovaj hardver (llmfit — kvantizacija izračunata po stroju):")
    names = render_llmfit_models(rows, out=out)
    choices = names + ["Preskoči — postavi kasnije"]
    idx = tui.prompt_choice("Odaberi JEDAN model:", choices, default=0,
                            input_fn=input_fn, out=out)
    if idx == len(names):
        return True
    model = names[idx]

    out(f"Skidam {model} (prekid je siguran — nastavlja gdje je stalo)...")
    if not preflight.ollama_pull(model, url, out=out):
        out("Model nije skinut. Pokreni setup ponovno ili postavi kasnije u Postavkama.")
        return tui.prompt_yes_no("Nastavi setup bez modela?", default=True,
                                 input_fn=input_fn, out=out)

    emb = setup_embedding(spine, cfg, out=out)
    from ragspine.business import model_settings
    model_settings.save(spine, "ollama", model=model, ollama_url=url,
                        embed_model=emb or "", user="setup")
    if not self_test(spine, cfg, input_fn=input_fn, out=out):
        out("⚠ Self-test nije prošao — model je spremljen, provjeri ga kasnije u Postavkama.")
    return True


def run(spine, cfg, *, input_fn=input, out=print) -> None:
    if wizard_state.is_complete(spine):
        out("Setup je već dovršen. Za ponovno: `ragspine setup --reset`.")
        return
    stage = wizard_state.get_stage(spine)
    out(f"RAGSPINE setup (nastavak od koraka {stage + 1}).")
    try:
        if stage < 1:
            if not page_preduvjeti(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na preduvjetima. Pokreni ponovno kad popraviš.")
                return
            wizard_state.set_stage(spine, 1)
        if stage < 2:
            if not page_operater(spine, input_fn=input_fn, out=out):
                out("Setup prekinut na operateru.")
                return
            wizard_state.set_stage(spine, 2)
        if stage < 3:
            if not page_model(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na modelu. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 3)
    except (EOFError, KeyboardInterrupt):
        # non-TTY / piped stdin (npr. servis bez terminala) — bez tracebacka.
        # ponytail: run() ostaje `-> None`; pozivatelj (_cmd_setup) ne detektira
        # ovaj slučaj posebno pa CLI izlazi s 0. Upgrade path: vratiti bool/kod
        # ako se pokaže da nešto ovisi o exit statusu.
        out("")
        out("Setup zahtijeva interaktivni terminal. Pokreni `ragspine setup` u terminalu; "
            "stanje je spremljeno — nastavlja gdje je stao.")
        return
    # P2 pokriva stranice 1-3; mark_complete se pomiče dalje kako stranice
    # 4-6 stižu u P3-P4 (poziv ide iza ZADNJE implementirane stranice).
    wizard_state.mark_complete(spine)
    out("P2 gotov: preduvjeti + operater + model. Setup je dovršen — web sučelje je dostupno.")
    out("Stranice 4-6 (mreža/HTTPS/servis, mape, sažetak) slijede u P3-P4.")
