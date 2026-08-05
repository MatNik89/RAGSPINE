"""Terminal setup wizard. Jedan fiksni slijed, resume preko wizard_state.
P1: Stranica 1 (preduvjeti) + Stranica 2 (operater). Ostale stranice u P2-P4."""
from ragspine.ops import preflight, tui, wizard_state


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


def run(spine, cfg, *, input_fn=input, out=print) -> None:
    if wizard_state.is_complete(spine):
        out("Setup je već dovršen. Za ponovno: `ragspine setup --reset`.")
        return
    stage = wizard_state.get_stage(spine)
    out(f"RAGSPINE setup (nastavak od koraka {stage + 1}).")
    if stage < 1:
        if not page_preduvjeti(spine, cfg, input_fn=input_fn, out=out):
            out("Setup prekinut na preduvjetima. Pokreni ponovno kad popraviš.")
            return
        wizard_state.set_stage(spine, 1)
    # Stranica 2 (operater) — Task 8 nadograđuje ovdje.
    out("Preduvjeti u redu. (Stranica 2 slijedi u Task 8.)")
