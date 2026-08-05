# tests/test_tui.py
from ragspine.ops import tui


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
