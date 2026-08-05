"""Terminal setup wizard. Jedan fiksni slijed, resume preko wizard_state.
P1: Stranica 1 (preduvjeti) + Stranica 2 (operater). Ostale stranice u P2-P4."""
from ragspine.ops import preflight, tui, wizard_state
from ragspine.web import firstrun

_MIN_PW = 8


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
    except (EOFError, KeyboardInterrupt):
        # non-TTY / piped stdin (npr. servis bez terminala) — bez tracebacka.
        # ponytail: run() ostaje `-> None`; pozivatelj (_cmd_setup) ne detektira
        # ovaj slučaj posebno pa CLI izlazi s 0. Upgrade path: vratiti bool/kod
        # ako se pokaže da nešto ovisi o exit statusu.
        out("")
        out("Setup zahtijeva interaktivni terminal. Pokreni `ragspine setup` u terminalu; "
            "stanje je spremljeno — nastavlja gdje je stao.")
        return
    out("P1 gotov: preduvjeti + operater. Stranice 3-6 slijede u P2-P4.")
