# TUI face-lift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wizard dobiva curses radiolist/checklist (strelice+Enter, ESC natrag, boje) s numeriranim fallbackom; stranica 3 tablicu modela s Disk stupcem i rangiranim namjenama; stranica 2 skriveni unos lozinke.

**Architecture:** Novi čisti UI modul `atlas/ops/tui_curses.py` (port jezgre hermes `curses_ui.py`, bez fuzzy searcha) + čisti data modul `atlas/ops/model_table.py` (disk procjena, namjene katalog, poravnanje stupaca). Wizard stranice 1 i 3 prelaze na radiolist; potpisi stranica nepromijenjeni. Testovi idu isključivo fallback putem (nema TTY-ja); curses dekodiranje se testira lažnim stdscr-om.

**Tech Stack:** Python 3.11+, stdlib curses (Windows: windows-curses — jedina nova ovisnost), pytest.

## Global Constraints

- Hrvatski latinica s dijakriticima; NIKAD ćirilica.
- Jedina dopuštena nova ovisnost: `windows-curses>=2.3; sys_platform == "win32"`.
- Testovi bez mreže/stdina/pravih subprocessa; curses NIKAD u testovima (nema TTY-ja).
- Puni suite u prvom planu prije svakog commita (`python -m pytest -q`, prije grane: 1156 passed, 1 skipped).
- Hrvatske konvencionalne commit poruke + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Potpisi wizard stranica `(spine, cfg, *, input_fn, out)` se NE mijenjaju.

---

### Task 1: tui_curses.py — jezgra (dekodiranje, event loop, radiolist, checklist, fallbacki)

**Files:**
- Create: `atlas/ops/tui_curses.py`
- Test: `tests/test_tui_curses.py`

**Interfaces:**
- Produces:
  - `radiolist(title, items, *, selected=0, header="", cancel_returns=None, input_fn=input, out=print) -> int | None` — vrati indeks; ESC/odustajanje vrati `cancel_returns` (None ako nije zadan). `header` = višelinijski tekst iznad stavki (npr. zaglavlje tablice).
  - `checklist(title, items, selected, *, input_fn=input, out=print) -> set[int]` — razmaknica toggle; ESC vrati početni `selected`.
  - `_decode_menu_key(stdscr, key) -> str` (NAV_* konstante) — za unit testove.
  - `_use_curses(input_fn) -> bool` — curses put SAMO kad je `input_fn is builtins.input` ∧ stdin i stdout TTY ∧ curses importabilan.

- [ ] **Step 1: napiši failing testove**

`tests/test_tui_curses.py`:

```python
"""tui_curses: dekodiranje tipki (lažni stdscr) + numerirani fallbacki."""
import builtins

from atlas.ops import tui_curses as tc


class _FakeScr:
    """Skriptirani getch niz; timeout() se samo bilježi."""
    def __init__(self, keys):
        self.keys = list(keys)
        self.timeouts = []

    def getch(self):
        return self.keys.pop(0) if self.keys else -1

    def timeout(self, ms):
        self.timeouts.append(ms)


def test_decode_strelice_key_konstante():
    import curses
    assert tc._decode_menu_key(_FakeScr([]), curses.KEY_UP) == tc.NAV_UP
    assert tc._decode_menu_key(_FakeScr([]), curses.KEY_DOWN) == tc.NAV_DOWN


def test_decode_jk_enter_space_q():
    assert tc._decode_menu_key(_FakeScr([]), ord("k")) == tc.NAV_UP
    assert tc._decode_menu_key(_FakeScr([]), ord("j")) == tc.NAV_DOWN
    assert tc._decode_menu_key(_FakeScr([]), 10) == tc.NAV_SELECT
    assert tc._decode_menu_key(_FakeScr([]), 13) == tc.NAV_SELECT
    assert tc._decode_menu_key(_FakeScr([]), ord(" ")) == tc.NAV_TOGGLE
    assert tc._decode_menu_key(_FakeScr([]), ord("q")) == tc.NAV_CANCEL


def test_decode_lone_esc_je_cancel():
    scr = _FakeScr([])          # nema nastavka → getch vrati -1
    assert tc._decode_menu_key(scr, 27) == tc.NAV_CANCEL
    assert scr.timeouts[0] == 60   # kratki timeout za split sekvence
    assert scr.timeouts[-1] == -1  # blocking mode vraćen


def test_decode_csi_strelice_nisu_cancel():
    assert tc._decode_menu_key(_FakeScr([ord("["), ord("A")]), 27) == tc.NAV_UP
    assert tc._decode_menu_key(_FakeScr([ord("["), ord("B")]), 27) == tc.NAV_DOWN
    assert tc._decode_menu_key(_FakeScr([ord("O"), ord("A")]), 27) == tc.NAV_UP


def test_decode_nepoznata_csi_progutana_do_terminatora():
    # ESC [ 3 ~  (Delete) — mora pojesti SVE bajtove i vratiti NONE
    scr = _FakeScr([ord("["), ord("3"), ord("~")])
    assert tc._decode_menu_key(scr, 27) == tc.NAV_NONE
    assert scr.keys == []


def test_use_curses_odbija_injektirani_input_fn():
    assert tc._use_curses(lambda _="": "x") is False


def test_use_curses_odbija_ne_tty(monkeypatch):
    # builtins.input, ali stdin u testu nije TTY → fallback
    assert tc._use_curses(builtins.input) is False


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def test_radiolist_fallback_odabir_broja():
    lines = []
    idx = tc.radiolist("Naslov", ["prva", "druga", "treća"],
                       input_fn=_reader("2"), out=lines.append)
    assert idx == 1
    text = "\n".join(lines)
    assert "Naslov" in text and "1." in text and "3." in text


def test_radiolist_fallback_enter_default():
    idx = tc.radiolist("N", ["a", "b"], selected=1,
                       input_fn=_reader(""), out=lambda *_: None)
    assert idx == 1


def test_radiolist_fallback_nevaljan_pa_valjan():
    lines = []
    idx = tc.radiolist("N", ["a", "b"], input_fn=_reader("9", "x", "1"),
                       out=lines.append)
    assert idx == 0
    assert any("Neispravan" in l for l in lines)


def test_radiolist_fallback_header_ispisan():
    lines = []
    tc.radiolist("N", ["a"], header="Kol1  Kol2", input_fn=_reader("1"),
                 out=lines.append)
    assert any("Kol1  Kol2" in l for l in lines)


def test_radiolist_fallback_eof_vraca_cancel():
    def _boom(_=""):
        raise EOFError
    assert tc.radiolist("N", ["a", "b"], cancel_returns=1,
                        input_fn=_boom, out=lambda *_: None) == 1
    assert tc.radiolist("N", ["a"], input_fn=_boom, out=lambda *_: None) is None


def test_checklist_fallback_toggle_pa_enter():
    got = tc.checklist("N", ["a", "b", "c"], set(),
                       input_fn=_reader("1", "3", ""), out=lambda *_: None)
    assert got == {0, 2}


def test_checklist_fallback_toggle_off():
    got = tc.checklist("N", ["a", "b"], {0, 1},
                       input_fn=_reader("2", ""), out=lambda *_: None)
    assert got == {0}


def test_checklist_fallback_eof_vraca_pocetni():
    def _boom(_=""):
        raise EOFError
    assert tc.checklist("N", ["a", "b"], {1}, input_fn=_boom,
                        out=lambda *_: None) == {1}
```

- [ ] **Step 2: run — očekuj FAIL** (`python -m pytest tests/test_tui_curses.py -q`; modul ne postoji)

- [ ] **Step 3: implementacija `atlas/ops/tui_curses.py`**

```python
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
```

- [ ] **Step 4: run testova + puni suite**

`python -m pytest tests/test_tui_curses.py -q` → PASS; `python -m pytest -q` → 1156+15 passed.

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/tui_curses.py tests/test_tui_curses.py
git commit -m "feat(tui): curses radiolist/checklist s ESC dekodiranjem i numeriranim fallbackom"
```

---

### Task 2: prompt_password (getpass) + stranica 2

**Files:**
- Modify: `atlas/ops/tui.py` (dodaj prompt_password)
- Modify: `atlas/ops/wizard.py` — `page_operater`: lozinka + ponovi idu kroz `tui.prompt_password`
- Test: `tests/test_tui.py` (dodaj), `tests/test_wizard.py` (postojeći testovi lozinke moraju i dalje proći — fallback prima input_fn)

**Interfaces:**
- Produces: `tui.prompt_password(question, *, input_fn=input, out=print) -> str` — getpass (skriveno) kad je `input_fn is builtins.input` i stdin TTY; inače `input_fn(f"{question}: ").strip()`.

- [ ] **Step 1: failing testovi** (dodaj u `tests/test_tui.py`):

```python
def test_prompt_password_injektirani_input_fn_vidljiv():
    from atlas.ops import tui
    got = tui.prompt_password("Lozinka", input_fn=lambda _="": " tajna123 ",
                              out=lambda *_: None)
    assert got == "tajna123"


def test_prompt_password_tty_koristi_getpass(monkeypatch):
    import builtins
    import getpass as gp
    import sys as _sys
    from atlas.ops import tui
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(gp, "getpass", lambda prompt="": "skriveno")
    got = tui.prompt_password("Lozinka", input_fn=builtins.input,
                              out=lambda *_: None)
    assert got == "skriveno"
```

- [ ] **Step 2: run — FAIL** (`python -m pytest tests/test_tui.py -q`)

- [ ] **Step 3: implementacija**

U `atlas/ops/tui.py` (vrh datoteke dobiva `import builtins` i `import sys`):

```python
def prompt_password(question: str, *, input_fn=input, out=print) -> str:
    """Skriveni unos lozinke na pravom TTY-ju (getpass — ne ostaje u
    scrollbacku); fallback vidljivi unos kroz input_fn (testovi, ne-TTY)."""
    if input_fn is builtins.input:
        try:
            if sys.stdin.isatty():
                import getpass
                return getpass.getpass(f"{question}: ").strip()
        except Exception:
            pass   # egzotični terminal bez getpass podrške → vidljivi unos
    return input_fn(f"{question}: ").strip()
```

U `atlas/ops/wizard.py` `page_operater`: zamijeni
`pw = tui.prompt_text("Lozinka (min 8)", ...)` s
`pw = tui.prompt_password("Lozinka (min 8)", input_fn=input_fn, out=out)` i
`pw2 = tui.prompt_text("Ponovi lozinku", ...)` s
`pw2 = tui.prompt_password("Ponovi lozinku", input_fn=input_fn, out=out)`.

- [ ] **Step 4: run + puni suite** (`python -m pytest tests/test_tui.py tests/test_wizard.py -q` pa `python -m pytest -q`)

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/tui.py atlas/ops/wizard.py tests/test_tui.py
git commit -m "feat(wizard): skriveni unos lozinke (getpass) s vidljivim fallbackom za testove"
```

---

### Task 3: model_table.py — disk procjena, rangirane namjene, poravnanje

**Files:**
- Create: `atlas/ops/model_table.py`
- Test: `tests/test_model_table.py`

**Interfaces:**
- Consumes: llmfit retke iz `preflight.llmfit_models` (ključevi: ollama_name, params, best_quant, memory_gb, tps, fit_label, use_case).
- Produces:
  - `disk_gb(params: str, quant: str) -> float` — 0.0 = nepoznato
  - `namjene(ollama_name: str, use_case: str = "") -> str` — "chat › sažimanje › reasoning"
  - `table_rows(rows) -> tuple[str, list[str]]` — (zaglavlje, poravnati retci; red i odgovara rows[i]; prvi red nosi ⭐)

- [ ] **Step 1: failing testovi**

`tests/test_model_table.py`:

```python
"""model_table: disk procjena, rangirane namjene, poravnanje tablice."""
from atlas.ops import model_table as mt


def test_disk_gb_q4_7b():
    # 7B × 4.6 bita / 8 × 1.08 ≈ 4.3 GB
    assert 3.5 < mt.disk_gb("7B", "Q4_K_M") < 5.0


def test_disk_gb_q2_manji_od_q8():
    assert mt.disk_gb("7B", "Q2_K") < mt.disk_gb("7B", "Q8_0")


def test_disk_gb_milijuni_parametara():
    # 135M model — disk ispod pola GB
    assert 0 < mt.disk_gb("135M", "Q4_K_M") < 0.5


def test_disk_gb_nepoznato_vraca_nulu():
    assert mt.disk_gb("", "Q4_K_M") == 0.0
    assert mt.disk_gb("7B", "") == 0.0
    assert mt.disk_gb("čudno", "Q4") == 0.0


def test_namjene_rangirane_po_obitelji():
    assert mt.namjene("qwen2.5:7b").startswith("chat")
    assert "›" in mt.namjene("qwen2.5:7b")
    assert mt.namjene("deepseek-r1:7b").startswith("reasoning")
    assert mt.namjene("qwen2.5-coder:7b").startswith("kod")


def test_namjene_fallback_na_use_case():
    assert mt.namjene("nepoznati-model:1b", "brzi asistent") == "brzi asistent"
    assert mt.namjene("nepoznati-model:1b", "") == "chat"


def test_table_rows_poravnanje_i_zvjezdica():
    rows = [
        {"ollama_name": "qwen2.5:7b", "params": "7B", "best_quant": "Q4_K_M",
         "memory_gb": 5.2, "tps": 11.0, "fit_label": "Good", "use_case": ""},
        {"ollama_name": "phi3:mini", "params": "3.8B", "best_quant": "Q4_K_M",
         "memory_gb": 3.1, "tps": 18.0, "fit_label": "Marginal", "use_case": ""},
    ]
    header, lines = mt.table_rows(rows)
    assert len(lines) == 2
    for col in ("Naziv", "Param", "Kvant", "RAM", "Disk", "Brzina", "Namjena"):
        assert col in header
    assert "⭐" in lines[0] and "⭐" not in lines[1]
    assert "🟢" in lines[0] and "🟡" in lines[1]
    assert "~5.2 GB" in lines[0].replace(",", ".")
    # Disk stupac popunjen procjenom, ne '?'
    assert lines[0].count("GB") >= 2


def test_table_rows_prazno():
    header, lines = mt.table_rows([])
    assert lines == [] and "Naziv" in header
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: implementacija `atlas/ops/model_table.py`**

```python
"""Tablica modela za stranicu 3 wizarda: disk procjena iz kvantizacije,
rangirane namjene po obitelji modela, poravnanje stupaca. Čisti modul —
bez I/O-a, potpuno unit-testabilan."""

# bita po težini za GGUF kvantizacije (K-kvantovi imaju mješovite blokove pa
# su efektivno malo iznad nominale); ručno kurirano, gruba procjena je cilj
_QUANT_BITS = [
    ("q2", 2.6), ("q3", 3.4), ("q4", 4.6), ("q5", 5.6), ("q6", 6.6),
    ("q8", 8.5), ("f16", 16.0), ("fp16", 16.0), ("bf16", 16.0), ("f32", 32.0),
]

# Rangirane namjene po obitelji (1. najjača). Ključ = substring ollama imena;
# redoslijed bitan (coder prije qwen). llmfit use_case je fallback.
_NAMJENE = [
    ("deepseek-r1", ["reasoning", "kod", "chat"]),
    ("qwen2.5-coder", ["kod", "chat"]),
    ("codellama", ["kod", "chat"]),
    ("granite-code", ["kod", "chat"]),
    ("qwen", ["chat", "hrvatski", "sažimanje", "reasoning"]),
    ("llama3", ["chat", "sažimanje", "hrvatski"]),
    ("phi4", ["reasoning", "sažimanje", "chat"]),
    ("phi3", ["sažimanje", "chat"]),
    ("mistral", ["chat", "sažimanje", "kod"]),
    ("gemma", ["chat", "sažimanje", "hrvatski"]),
    ("smollm", ["chat"]),
    ("granite", ["chat", "kod"]),
]

_COLS = ["Naziv", "Param", "Kvant", "RAM", "Disk", "Brzina", "Namjena"]
_PILL = {"Good": "🟢", "Marginal": "🟡"}


def _params_b(params: str) -> float:
    """'7B' / '3.8B' / '135M' → milijarde parametara; 0.0 = nepoznato."""
    s = str(params).strip().upper()
    try:
        if s.endswith("M"):
            return float(s[:-1]) / 1000.0
        return float(s.rstrip("B"))
    except ValueError:
        return 0.0


def disk_gb(params: str, quant: str) -> float:
    """Procjena GGUF datoteke na disku: params × bita/8 × 1.08 režije.
    0.0 kad procjena nije moguća (prikaz '?'). RAM ≠ disk — llmfit-ov
    memory_gb uključuje KV cache/režiju, ovo je download/pohrana."""
    b = _params_b(params)
    q = str(quant).lower()
    bits = next((v for prefix, v in _QUANT_BITS if q.startswith(prefix)), 0.0)
    if not b or not bits:
        return 0.0
    return round(b * bits / 8 * 1.08, 1)


def namjene(ollama_name: str, use_case: str = "") -> str:
    """Rangirani prikaz namjena ('kod › chat'); fallback llmfit use_case."""
    name = (ollama_name or "").lower()
    for key, uses in _NAMJENE:
        if key in name:
            return " › ".join(uses)
    return (use_case or "chat").strip()


def table_rows(rows) -> tuple[str, list[str]]:
    """(zaglavlje, poravnati retci) za radiolist; red i odgovara rows[i].
    Prvi red (najbolji llmfit score) nosi ⭐ preporuku."""
    data = []
    for i, r in enumerate(rows):
        d = disk_gb(r.get("params", ""), r.get("best_quant", ""))
        star = " ⭐" if i == 0 else ""
        data.append([
            f"{_PILL.get(r.get('fit_label'), '?')} {r.get('ollama_name', '?')}{star}",
            str(r.get("params") or "?"),
            str(r.get("best_quant") or "?"),
            f"~{float(r.get('memory_gb') or 0):.1f} GB",
            f"~{d:.1f} GB" if d else "?",
            f"~{float(r.get('tps') or 0):.0f} tok/s",
            namjene(r.get("ollama_name", ""), r.get("use_case", "")),
        ])
    widths = [max([len(_COLS[c])] + [len(row[c]) for row in data])
              for c in range(len(_COLS))]
    header = "  ".join(h.ljust(widths[i]) for i, h in enumerate(_COLS)).rstrip()
    lines = ["  ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)).rstrip()
             for row in data]
    return header, lines
```

- [ ] **Step 4: run + puni suite**

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/model_table.py tests/test_model_table.py
git commit -m "feat(wizard): tablica modela — disk procjena po kvantizaciji i rangirane namjene"
```

---

### Task 4: wizard integracija — stranica 3 tablica + stranica 1 radiolist

**Files:**
- Modify: `atlas/ops/wizard.py` (`page_model`, `page_preduvjeti`; import `model_table`, `tui_curses`)
- Modify: `tests/test_wizard.py` (prilagodba postojećih testova stranica 1 i 3 na radiolist fallback unos — brojevi umjesto d/n)

**Interfaces:**
- Consumes: `tui_curses.radiolist` (Task 1), `model_table.table_rows` (Task 3), `preflight.system_state` (postoji: ram_free_gb/disk_free_gb/ram_total_gb).
- Produces: potpisi stranica NEPROMIJENJENI; `render_llmfit_models` se BRIŠE (zamijenjen tablicom — obriši i njegove testove).

- [ ] **Step 1: prilagodi/napiši testove**

U `tests/test_wizard.py`:
- obriši testove `render_llmfit_models` (funkcija nestaje);
- postojeće `page_model` testove prilagodi: gdje je unos bio `"1"`/`"2"` za prompt_choice, radiolist fallback također prima broj — provjeri da odgovor SAD cilja radiolist prompt ("Odaberi [1-N]"); zadnja stavka je i dalje "Preskoči — postavi kasnije";
- novi test — tablica i kontekst stroja u izlazu:

```python
def test_page_model_tablica_i_kontekst(tmp_path, monkeypatch):
    """Stranica 3 ispisuje slobodni RAM/disk i tablicu s Disk stupcem;
    odabir prvog reda pulla točan model."""
    s = init_spine(str(tmp_path / "t.db"))
    monkeypatch.setattr(wizard.preflight, "ollama_ready", lambda url: (True, "ok"))
    monkeypatch.setattr(wizard.preflight, "ollama_version", lambda url: "0.5.0")
    monkeypatch.setattr(wizard.preflight, "ollama_floor_ok", lambda v: True)
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ram_total_gb": 8.0, "ram_free_gb": 5.5,
                                        "disk_free_gb": 90.0})
    rows = [{"ollama_name": "qwen2.5:7b", "params": "7B", "best_quant": "Q4_K_M",
             "memory_gb": 5.2, "tps": 11.0, "fit_label": "Good", "use_case": ""}]
    monkeypatch.setattr(wizard.preflight, "llmfit_models", lambda cfg: rows)
    pulled = []
    monkeypatch.setattr(wizard.preflight, "ollama_pull",
                        lambda m, url, out=print: pulled.append(m) or True)
    monkeypatch.setattr(wizard, "setup_embedding", lambda s_, c, out=print: "emb")
    monkeypatch.setattr(wizard, "self_test",
                        lambda s_, c, input_fn=input, out=print: True)

    class _Cfg:
        ollama_url = "http://127.0.0.1:11434"
        embed_model = "x"
    lines = []
    ok = wizard.page_model(s, _Cfg(), input_fn=_reader("1"), out=lines.append)
    assert ok is True and pulled == ["qwen2.5:7b"]
    text = "\n".join(lines)
    assert "Disk" in text            # zaglavlje tablice
    assert "90" in text and "5.5" in text   # kontekst stroja
    assert "RAM, ne disk" in text    # legenda
```

- `page_preduvjeti` testovi: winget test sad bira "Auto-instaliraj: OCR" brojem;
  ne-Windows test bira "Prekini setup" brojem (page vrati False):

```python
def test_page_preduvjeti_offers_winget_install_on_windows(monkeypatch):
    """fail na Windowsu → radiolist nudi Auto-instaliraj; nakon pokušaja
    ponovno provjeri preduvjete."""
    reqs_fail = [{"key": "tesseract", "naziv": "OCR", "status": "fail",
                  "detalj": "nije pronađen", "fix": "winget install ..."}]
    reqs_ok = [{"key": "tesseract", "naziv": "OCR", "status": "ok", "detalj": "ok", "fix": ""}]
    seq = iter([reqs_fail, reqs_ok])
    monkeypatch.setattr(wizard.preflight, "requirements", lambda cfg: next(seq))
    monkeypatch.setattr(wizard.os, "name", "nt")
    calls = []
    monkeypatch.setattr(wizard.preflight, "install_via_winget",
                        lambda key, out=print: calls.append(key) or True)
    # radiolist (fail, Windows): 1=Auto-instaliraj: OCR, 2=Provjeri ponovno, 3=Prekini
    ok = wizard.page_preduvjeti(None, None, input_fn=_reader("1"), out=lambda *_: None)
    assert ok is True
    assert calls == ["tesseract"]


def test_page_preduvjeti_no_winget_offer_on_non_windows(monkeypatch):
    """Izvan Windowsa nema Auto-instaliraj stavke; Prekini vraća False."""
    reqs_fail = [{"key": "tesseract", "naziv": "OCR", "status": "fail",
                  "detalj": "nije pronađen", "fix": "apt install ..."}]
    monkeypatch.setattr(wizard.preflight, "requirements", lambda cfg: reqs_fail)
    monkeypatch.setattr(wizard.os, "name", "posix")
    lines = []
    # radiolist: 1=Provjeri ponovno, 2=Prekini setup
    ok = wizard.page_preduvjeti(None, None, input_fn=_reader("2"), out=lines.append)
    assert ok is False
    assert not any("Auto-instaliraj" in l for l in lines)
```

NAPOMENA: `monkeypatch.setattr(wizard.os, "name", ...)` je globalni patch os
modula — NE mijenjaj taj obrazac ovdje (postojeći stil), ali NE dodaji nove
testove koji os.name patchaju na "posix" pa pozivaju kod koji instancira
Path (Windows CI INTERNALERROR lekcija iz 825dd36).

- [ ] **Step 2: run — FAIL** (`python -m pytest tests/test_wizard.py -q`)

- [ ] **Step 3: implementacija u `atlas/ops/wizard.py`**

Import: `from atlas.ops import ..., model_table, tui_curses` (prošireni postojeći import red).

`page_preduvjeti` — zamijeni CIJELO tijelo petlje:

```python
def page_preduvjeti(spine, cfg, *, input_fn=input, out=print) -> bool:
    tui.print_header("1/6  Preduvjeti", out=out)
    while True:
        reqs = preflight.requirements(cfg)
        ok = render_preflight(reqs, out=out)
        # winget auto-install je Windows-only (drugdje ga wizard ne nudi — brief).
        instalabilni = [r for r in reqs
                        if os.name == "nt" and r["key"] in preflight.WINGET_IDS
                        and r["status"] in ("fail", "warn")]
        if ok and not instalabilni:
            return True
        out("")
        opcije, akcije = [], []
        if ok:
            opcije.append("Nastavi (obavezni preduvjeti ✓)")
            akcije.append("ok")
        for r in instalabilni:
            opcije.append(f"Auto-instaliraj: {r['naziv']}")
            akcije.append(("winget", r["key"]))
        opcije.append("Provjeri ponovno")
        akcije.append("retry")
        opcije.append("Prekini setup")
        akcije.append("stop")
        naslov = ("Neki obavezni preduvjeti nedostaju (✗) — što dalje?"
                  if not ok else "Preporuke (⚠) — što dalje?")
        idx = tui_curses.radiolist(naslov, opcije, selected=0,
                                   cancel_returns=len(akcije) - 1,
                                   input_fn=input_fn, out=out)
        akcija = akcije[idx]
        if akcija == "ok":
            return True
        if akcija == "stop":
            return False
        if isinstance(akcija, tuple):
            preflight.install_via_winget(akcija[1], out=out)
        # "retry" i post-install: petlja ponovno provjerava
```

Redoslijed opcija (fiksan, testovi ga prate): [Nastavi (samo kad ok)] +
[Auto-instaliraj: X po instalabilnom retku] + [Provjeri ponovno] +
[Prekini setup]. Fail+Windows: 1=Auto-instaliraj, 2=Provjeri, 3=Prekini.
Fail+ne-Windows: 1=Provjeri, 2=Prekini.

`page_model` — zamijeni blok od `out("Modeli za ovaj hardver...")` do
`model = names[idx]` s:

```python
    st = preflight.system_state(cfg)
    out(f"Stroj: slobodno ~{st.get('ram_free_gb', '?')} GB RAM / "
        f"~{st.get('disk_free_gb', '?')} GB diska "
        f"(ukupno {st.get('ram_total_gb', '?')} GB RAM)")
    out("Modeli za ovaj hardver (llmfit — kvantizacija izračunata po stroju):")
    header, lines = model_table.table_rows(rows)
    names = [r["ollama_name"] for r in rows]
    items = lines + ["Preskoči — postavi kasnije"]
    idx = tui_curses.radiolist(
        "Odaberi JEDAN model (🟢 komotno / 🟡 tijesno — RAM, ne disk):",
        items, selected=0, header=header,
        cancel_returns=len(names),          # ESC = preskoči
        input_fn=input_fn, out=out)
    if idx == len(names):
        return True
    model = names[idx]
```

Obriši funkciju `render_llmfit_models` i `_PILL_GLYPH` (zamijenjeni
model_table modulom).

- [ ] **Step 4: run + puni suite** (`python -m pytest tests/test_wizard.py -q` pa `python -m pytest -q`)

- [ ] **Step 5: Commit**

```bash
git add atlas/ops/wizard.py tests/test_wizard.py
git commit -m "feat(wizard): stranica 3 tablica modela s Disk stupcem i kontekstom stroja; stranica 1 radiolist izbori"
```

---

### Task 5: windows-curses ovisnost + dokument sljedeće grane

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Create: `docs/superpowers/plans/next-tui-grana.md`

**Interfaces:**
- Consumes: sve prethodno.

- [ ] **Step 1: pyproject** — u `[project] dependencies` dodaj redak:

```toml
  "windows-curses>=2.3; sys_platform == 'win32'",
```

(Jedina dopuštena nova ovisnost; na Linux/macOS se ne instalira. Bez nje na
Windowsu wizard i dalje radi kroz numerirani fallback — _use_curses guard.)

- [ ] **Step 2: docs/superpowers/plans/next-tui-grana.md** — popiši ostatak
TUI dorada IZVAN ovog opsega, s referencom na specifikacije:

```markdown
# Sljedeća TUI grana — ostatak face-lifta (spec: docs/e2e-nalazi-2026-08-06.md)

Napravljeno u grani tui-facelift: curses jezgra (radiolist/checklist + ESC
dekodiranje + fallback), tablica modela (Disk stupac, rangirane namjene,
kontekst stroja), radiolist izbori na stranici 1, getpass lozinka,
windows-curses dep.

Ostaje (redoslijedom prioriteta iz nalaza):
1. Folder picker (stranica 5): TUI preglednik mapa — diskovi + UNC upis
   jednom pa browsanje; Enter=uđi, Razmak/prvi red=odaberi, ESC/..=gore;
   GetDriveType upozorenje SAMO za DRIVE_REMOTE.
2. Živi izlaz podprocesa: winget najava veličine + progress; ollama pull
   poštovati \r (čitanje sirovog streama).
3. PATH refresh bez restarta (registry HKLM/HKCU) + poznate lokacije probe.
4. Tesseract auto-install: hrv.traineddata download, user PATH, "već
   instalirano" poruka.
5. install.ps1 uskladba (bez pitanja operatera, uputa na atlas setup).
6. Prečac na radnoj površini (sve platforme).
7. Cert bootstrap stranica (http://IP:8080/postavi) + prijateljsko ime
   (fritz.box/mDNS) u SAN + doslovna uputa za radnike na stranici 6/6.
8. Pull s kvant sufiksom (llmfit kvant ≠ registry default) + usporedba
   stvarne veličine.
9. bge-m3 feature-detect / fastembed upgrade; MODEL_CATALOG trim na 2-3
   fallback modela; kozmetika (fitz warning, miješane kose crte, getpass
   za `atlas auth add`).
```

- [ ] **Step 3: puni suite** (`python -m pytest -q`)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml docs/superpowers/plans/next-tui-grana.md
git commit -m "build: windows-curses (Windows only) + plan sljedeće TUI grane"
```
