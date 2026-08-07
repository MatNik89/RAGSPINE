# tests/test_tui.py
from atlas.ops import tui


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def test_prompt_choice_returns_index():
    assert tui.prompt_choice("Q", ["a", "b", "c"], input_fn=_reader("2"), out=lambda *_: None) == 1


def test_prompt_choice_empty_uses_default():
    assert tui.prompt_choice("Q", ["a", "b"], default=1, input_fn=_reader(""), out=lambda *_: None) == 1


def test_prompt_yes_no():
    assert tui.prompt_yes_no("Q", input_fn=_reader("da"), out=lambda *_: None) is True
    assert tui.prompt_yes_no("Q", input_fn=_reader("ne"), out=lambda *_: None) is False
    assert tui.prompt_yes_no("Q", default=False, input_fn=_reader(""), out=lambda *_: None) is False


def test_status_glyph():
    assert tui.status_glyph("ok") == "✓"
    assert tui.status_glyph("warn") == "⚠"
    assert tui.status_glyph("fail") == "✗"


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
