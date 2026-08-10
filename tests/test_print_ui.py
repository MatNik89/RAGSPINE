"""Ispis (D): @media print pravila + gumb za ispis na ključnim ekranima."""
from atlas.web import templates_ui
from atlas.web.templates_obveze import render_obveze


def test_print_css_present():
    css = templates_ui.CSS_TOKENS
    assert "@media print" in css
    assert ".no-print" in css and "display:none" in css


def test_print_button_is_no_print_and_calls_print():
    b = templates_ui.print_button("Ispis")
    assert "no-print" in b and "window.print()" in b


def test_obveze_page_has_print_button():
    html = render_obveze("PDV", "2026-08", rows=[])
    assert "window.print()" in html and "no-print" in html


def test_klijent_page_has_print_button():
    assert "window.print()" in templates_ui.klijent_page(1)
