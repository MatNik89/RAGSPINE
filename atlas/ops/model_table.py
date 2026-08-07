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
