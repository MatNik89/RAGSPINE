"""Trajni audit: staro ime ne smije natrag u KOD (docs povijest smije)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Kod = paket, testovi, install skripte, pyproject, extension.
CODE_TARGETS = ["atlas", "tests", "extension", "install.sh", "install.ps1",
                "pyproject.toml"]
STARO = re.compile("rag" "spine", re.I)  # razlomljeno da sam sebe ne ulovi
PRESKOCI_DIJELOVE = {"__pycache__", ".pytest_cache"}


def _datoteke():
    for top in CODE_TARGETS:
        p = ROOT / top
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in p.rglob("*"):
                if (f.is_file() and not f.suffix == ".pyc"
                        and not PRESKOCI_DIJELOVE & set(f.parts)
                        and "egg-info" not in str(f)):
                    yield f


def test_staro_ime_nije_u_kodu_bez_compat_markera():
    losi = []
    for f in _datoteke():
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if STARO.search(line) and "compat" not in line.lower():
                losi.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not losi, ("Staro ime u kodu bez 'compat' markera:\n"
                      + "\n".join(losi))
