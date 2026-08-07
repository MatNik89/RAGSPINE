"""Minimalni terminal-UI primitivi za setup wizard. I/O je injektabilan
(input_fn/out) radi testabilnosti — bez pravog stdina u testovima.
Uzor: NousResearch/hermes-agent hermes_cli/setup.py (prompt_choice/yes_no)."""
import builtins
import sys

_GLYPH = {"ok": "✓", "warn": "⚠", "fail": "✗"}


def status_glyph(status: str) -> str:
    return _GLYPH.get(status, "?")


def print_header(title: str, *, out=print) -> None:
    out("")
    out(f"── {title} " + "─" * max(0, 50 - len(title)))


def prompt_text(question: str, *, default: str = "", input_fn=input, out=print) -> str:
    suffix = f" [{default}]" if default else ""
    ans = input_fn(f"{question}{suffix}: ").strip()
    return ans or default


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


def prompt_yes_no(question: str, *, default: bool = True, input_fn=input, out=print) -> bool:
    hint = "[D/n]" if default else "[d/N]"
    ans = input_fn(f"{question} {hint}: ").strip().lower()
    if not ans:
        return default
    return ans in ("d", "da", "y", "yes")


def prompt_choice(question: str, choices: list[str], *, default: int = 0,
                  input_fn=input, out=print) -> int:
    out(question)
    for i, c in enumerate(choices, 1):
        out(f"  {i}. {c}")
    while True:
        ans = input_fn(f"Odaberi [1-{len(choices)}] (default {default + 1}): ").strip()
        if not ans:
            return default
        if ans.isdigit() and 1 <= int(ans) <= len(choices):
            return int(ans) - 1
        out("Neispravan izbor.")
