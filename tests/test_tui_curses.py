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
