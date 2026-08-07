"""TUI preglednik mapa za wizard (stranica 5, E2E nalaz: tipkanje UNC
putanja je mučenje). Nad tui_curses.radiolist: curses na TTY-ju, numerirani
fallback u testovima. Vraćena putanja NIJE validirana — pozivatelj
(page_mape) zadržava svoje UNC/isdir provjere."""
import os
import string

from atlas.ops import tui_curses

_ODABERI = "[✓ ODABERI OVU MAPU]"
_LEGENDA = "Enter = uđi u mapu · prvi red = odaberi OVU mapu · ESC/.. = gore"


def _roots() -> list[str]:
    """Windows: postojeći diskovi A:-Z:; POSIX: ~ i /."""
    if os.name == "nt":
        return [f"{d}:\\" for d in string.ascii_uppercase
                if os.path.exists(f"{d}:\\")]
    return [os.path.expanduser("~"), "/"]


def _subdirs(path: str) -> list[str]:
    """Podmape abecedno; nedostupne lokacije = prazno (bez pada)."""
    def _dir(e):
        try:
            return e.is_dir(follow_symlinks=False)
        except OSError:
            return False
    try:
        with os.scandir(path) as it:
            return sorted(e.name for e in it if _dir(e))
    except OSError:
        return []


def _browse(start: str, *, input_fn, out) -> str | None:
    cur = start
    while True:
        items = [_ODABERI, ".."] + _subdirs(cur)
        sel = tui_curses.radiolist(f"Mapa: {cur}", items, selected=0,
                                   header=_LEGENDA, cancel_returns=1,
                                   input_fn=input_fn, out=out)
        if sel == 0:
            return cur
        if sel == 1:
            parent = os.path.dirname(cur.rstrip("\\/"))
            if not parent or parent == cur:
                return None   # s vrha: izlaz (pozivatelj nudi ponovni ulaz)
            cur = parent
            continue
        cur = os.path.join(cur, items[sel])


def pick_folder(*, input_fn=input, out=print) -> str | None:
    """Početni ekran (diskovi/korijeni + mrežna lokacija + ručni upis) pa
    browsanje. None = odustao."""
    roots = _roots()
    items = (roots
             + ["Mrežna lokacija (\\\\server\\share — upiši jednom, pa browsaj)",
                "Ručni upis putanje (napredno)", "Odustani / preskoči"])
    idx = tui_curses.radiolist("Odaberi polazište:", items, selected=0,
                               cancel_returns=len(items) - 1,
                               input_fn=input_fn, out=out)
    if idx is None or idx == len(items) - 1:
        return None
    if idx == len(roots):       # mrežna lokacija
        try:
            share = input_fn("\\\\server\\share: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return _browse(share, input_fn=input_fn, out=out) if share else None
    if idx == len(roots) + 1:   # ručni upis
        try:
            return input_fn("Putanja: ").strip() or None
        except (EOFError, KeyboardInterrupt):
            return None
    return _browse(roots[idx], input_fn=input_fn, out=out)
