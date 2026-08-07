"""Curses UI za setup wizard — radiolist/checklist s numeriranim fallbackom.

Port jezgre NousResearch/hermes-agent hermes_cli/curses_ui.py (dekodiranje
ESC/CSI sekvenci, zajednički event loop, flush stdina); bez fuzzy searcha.
Fallback (nema TTY-ja / nema cursesa / injektiran input_fn) je JEDINI put
kojim idu testovi: deterministički, bez pravog stdina."""
import builtins
import sys

NAV_UP = "up"
NAV_DOWN = "down"
NAV_SELECT = "select"
NAV_TOGGLE = "toggle"
NAV_CANCEL = "cancel"
NAV_NONE = "none"

_KEEP = object()


def _decode_menu_key(stdscr, key) -> str:
    """Normaliziraj pritisak u NAV_* akciju. Lone ESC = cancel; CSI/SS3
    strelice (i split preko sporog PTY-ja — timeout 60 ms) se dekodiraju,
    ostale sekvence se pojedu do terminatora da ne cure u sljedeći input()."""
    import curses
    if key in (curses.KEY_UP, ord("k")):
        return NAV_UP
    if key in (curses.KEY_DOWN, ord("j")):
        return NAV_DOWN
    if key in (curses.KEY_ENTER, 10, 13):
        return NAV_SELECT
    if key == ord(" "):
        return NAV_TOGGLE
    if key == ord("q"):
        return NAV_CANCEL
    if key == 27:
        try:
            stdscr.timeout(60)
            nxt = stdscr.getch()
        finally:
            stdscr.timeout(-1)
        if nxt == -1:
            return NAV_CANCEL          # pravi, usamljeni ESC
        if nxt in (ord("["), ord("O")):
            final = stdscr.getch()
            if final == ord("A"):
                return NAV_UP
            if final == ord("B"):
                return NAV_DOWN
            while 0x20 <= final <= 0x3F:   # CSI parametarski bajtovi
                final = stdscr.getch()
            return NAV_NONE
        return NAV_NONE
    return NAV_NONE


def _flush_stdin() -> None:
    """Nakon cursesa isprazni OS input buffer — zaostali escape bajtovi bi
    tiho pojeli/pokvarili sljedeći input() (hermes lekcija)."""
    try:
        if not sys.stdin.isatty():
            return
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass   # Windows / egzotični terminali: nema termios — preskoči


def _use_curses(input_fn) -> bool:
    """Curses SAMO s pravim builtins.input i pravim TTY-jem; svaki
    injektirani input_fn (testovi, web-bridge) ide fallbackom."""
    if input_fn is not builtins.input:
        return False
    try:
        import curses  # noqa: F401  (na Windowsu treba windows-curses)
    except Exception:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _run_menu(*, title, header, item_count, initial_cursor, draw_row,
              on_action, cancel_value, hint):
    """Zajednički curses event loop (poziva se tek IZA _use_curses provjere).
    draw_row(stdscr, y, i, is_cursor, max_x); on_action(action, cursor) vrati
    _KEEP za nastavak ili konačnu vrijednost."""
    import curses
    result = [cancel_value]
    header_lines = header.splitlines() if header else []

    def _draw(stdscr):
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
        cursor = initial_cursor
        scroll = 0
        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()
            row = 0
            try:
                hattr = curses.A_BOLD | (curses.color_pair(2)
                                         if curses.has_colors() else 0)
                stdscr.addnstr(row, 0, title, max_x - 1, hattr)
                row += 1
                stdscr.addnstr(row, 0, hint, max_x - 1, curses.A_DIM)
                row += 1
                for hl in header_lines:
                    if row >= max_y - 2:
                        break
                    stdscr.addnstr(row, 0, hl, max_x - 1, curses.A_DIM)
                    row += 1
            except curses.error:
                pass
            items_start = row + 1
            visible = max(1, max_y - items_start - 1)
            if cursor < scroll:
                scroll = cursor
            elif cursor >= scroll + visible:
                scroll = cursor - visible + 1
            for di, i in enumerate(range(scroll, min(item_count, scroll + visible))):
                draw_row(stdscr, di + items_start, i, i == cursor, max_x)
            stdscr.refresh()
            action = _decode_menu_key(stdscr, stdscr.getch())
            if action == NAV_UP:
                cursor = (cursor - 1) % item_count
            elif action == NAV_DOWN:
                cursor = (cursor + 1) % item_count
            elif action in (NAV_SELECT, NAV_TOGGLE, NAV_CANCEL):
                outcome = on_action(action, cursor)
                if outcome is not _KEEP:
                    result[0] = outcome
                    return

    try:
        curses.wrapper(_draw)
        _flush_stdin()
        return result[0]
    except KeyboardInterrupt:
        return cancel_value
    except Exception:
        return None   # signal pozivatelju: curses pukao → on zove fallback


# Sentinel: razlikuje "curses vratio None kao rezultat" od "curses pukao".
_CURSES_FAILED = object()


def radiolist(title, items, *, selected=0, header="", cancel_returns=None,
              input_fn=input, out=print):
    """Jedan izbor. Vrati indeks stavke; ESC/q/odustao → cancel_returns."""
    if _use_curses(input_fn):
        chosen = _radiolist_curses(title, items, selected, header, cancel_returns)
        if chosen is not _CURSES_FAILED:
            return chosen
    return _radiolist_fallback(title, items, selected, header, cancel_returns,
                               input_fn, out)


def _radiolist_curses(title, items, selected, header, cancel_returns):
    import curses

    def _draw_row(stdscr, y, i, is_cursor, max_x):
        line = f" {'→' if is_cursor else ' '} ({'●' if i == selected else '○'}) {items[i]}"
        attr = curses.A_NORMAL
        if is_cursor:
            attr = curses.A_BOLD | (curses.color_pair(1)
                                    if curses.has_colors() else 0)
        try:
            stdscr.addnstr(y, 0, line, max_x - 1, attr)
        except curses.error:
            pass

    sentinel = object()   # cancel_returns može biti None — ne smije se
                          # pomiješati s None kojim _run_menu javlja pad cursesa

    def _on_action(action, cursor):
        if action in (NAV_SELECT, NAV_TOGGLE):
            return cursor
        return sentinel   # NAV_CANCEL

    got = _run_menu(title=title, header=header, item_count=len(items),
                    initial_cursor=min(selected, len(items) - 1),
                    draw_row=_draw_row, on_action=_on_action,
                    cancel_value=sentinel,
                    hint="  ↑↓ kretanje  Enter potvrda  ESC odustani")
    if got is None:
        return _CURSES_FAILED   # curses pukao na pravom TTY-ju → fallback
    return cancel_returns if got is sentinel else got


def _radiolist_fallback(title, items, selected, header, cancel_returns,
                        input_fn, out):
    out("")
    out(title)
    if header:
        for hl in header.splitlines():
            out(f"      {hl}")
    for i, label in enumerate(items, 1):
        marker = "(●)" if i - 1 == selected else "(○)"
        out(f"  {marker} {i:>2}. {label}")
    while True:
        try:
            ans = input_fn(f"Odaberi [1-{len(items)}] (Enter = {selected + 1}): ").strip()
        except (EOFError, KeyboardInterrupt):
            return cancel_returns
        if not ans:
            return selected
        if ans.isdigit() and 1 <= int(ans) <= len(items):
            return int(ans) - 1
        out("Neispravan izbor.")


def checklist(title, items, selected, *, input_fn=input, out=print):
    """Više izbora (razmaknica toggle). ESC vrati POČETNI selected."""
    initial = set(selected)
    if _use_curses(input_fn):
        got = _checklist_curses(title, items, initial)
        if got is not _CURSES_FAILED:
            return got
    return _checklist_fallback(title, items, initial, input_fn, out)


def _checklist_curses(title, items, initial):
    import curses
    chosen = set(initial)

    def _draw_row(stdscr, y, i, is_cursor, max_x):
        line = f" {'→' if is_cursor else ' '} [{'✓' if i in chosen else ' '}] {items[i]}"
        attr = curses.A_NORMAL
        if is_cursor:
            attr = curses.A_BOLD | (curses.color_pair(1)
                                    if curses.has_colors() else 0)
        try:
            stdscr.addnstr(y, 0, line, max_x - 1, attr)
        except curses.error:
            pass

    def _on_action(action, cursor):
        if action == NAV_TOGGLE:
            chosen.symmetric_difference_update({cursor})
            return _KEEP
        if action == NAV_SELECT:
            return set(chosen)
        return set(initial)   # NAV_CANCEL

    got = _run_menu(title=title, header="", item_count=len(items),
                    initial_cursor=0, draw_row=_draw_row, on_action=_on_action,
                    cancel_value=set(initial),
                    hint="  ↑↓ kretanje  RAZMAK označi  Enter potvrda  ESC odustani")
    if got is None:
        return _CURSES_FAILED
    return got


def _checklist_fallback(title, items, initial, input_fn, out):
    chosen = set(initial)
    out("")
    out(title)
    while True:
        for i, label in enumerate(items, 1):
            out(f"  [{'✓' if i - 1 in chosen else ' '}] {i:>2}. {label}")
        try:
            ans = input_fn("Broj = označi/odznači, Enter = potvrdi: ").strip()
        except (EOFError, KeyboardInterrupt):
            return set(initial)
        if not ans:
            return chosen
        if ans.isdigit() and 1 <= int(ans) <= len(items):
            chosen.symmetric_difference_update({int(ans) - 1})
        else:
            out("Neispravan izbor.")
