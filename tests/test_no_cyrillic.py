"""Ćirilični homoglifi (о/а/е/с… izgledaju identično latinici) tiho ulaze u
kod i stringove kroz LLM-generirani tekst; grep ih ne nađe, identifieri pucaju.
Ovaj gate skenira sve tracked tekstualne datoteke."""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_TEXT_EXT = {".py", ".md", ".txt", ".toml", ".yml", ".yaml", ".json", ".html", ".js", ".css"}


def test_no_cyrillic_homoglyphs_in_repo():
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                           text=True, check=True).stdout.splitlines()
    offenders = []
    for rel in files:
        p = REPO / rel
        if p.suffix.lower() not in _TEXT_EXT or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = _CYRILLIC.search(line)
            if m:
                offenders.append(f"{rel}:{lineno}: {m.group(0)!r} u: {line.strip()[:80]}")
    assert not offenders, "ćirilični homoglifi:\n" + "\n".join(offenders)
