"""Local-hardware model recommender: given RAM/GPU, suggest per-role Ollama
models + a ready-to-paste LiteLLM config. GDPR-conscious offline setups only —
model tags are suggestions; the operator picks and pulls (ponytail: no
auto-pull, no telemetry)."""
import re
import shutil
import urllib.request

from ragspine.core.subproc import run_isolated

_WARN_NEED_8GB = "treba ≥8GB RAM/VRAM za lokalni model ove uloge"
_WARN_NEED_VLM = "treba jaci hardver za lokalni vizualni model (VLM)"

TIERS = {
    "tiny": {
        "chat": {"warn": _WARN_NEED_8GB},
        "embed": {"model": "nomic-embed-text", "note": "laganо ugrađivanje, radi i na CPU-u"},
        "utility": {"warn": _WARN_NEED_8GB},
        "vlm": {"warn": _WARN_NEED_VLM},
    },
    "small": {
        "chat": {"model": "qwen2.5:7b", "note": "mali chat model, ugodan na 8-16GB"},
        "embed": {"model": "bge-m3", "note": "višejezično ugrađivanje (dobro za HR)"},
        "utility": {"model": "qwen2.5:7b", "note": "isti mali model za pomoćne zadatke"},
        "vlm": {"warn": _WARN_NEED_VLM},
    },
    "medium": {
        "chat": {"model": "qwen2.5:14b", "note": "srednji chat model, bolja kvaliteta"},
        "embed": {"model": "bge-m3", "note": "višejezično ugrađivanje (dobro za HR)"},
        "utility": {"model": "qwen2.5:7b", "note": "manji model za brze pomoćne zadatke"},
        "vlm": {"model": "llava:7b", "note": "mali vizualni model za čitanje dokumenata/slika"},
    },
    "large": {
        "chat": {"model": "qwen2.5:32b", "note": "veliki chat model, visoka kvaliteta"},
        "embed": {"model": "bge-m3", "note": "višejezično ugrađivanje (dobro za HR)"},
        "utility": {"model": "qwen2.5:14b", "note": "srednji model za pomoćne zadatke"},
        "vlm": {"model": "llava:13b", "note": "veći vizualni model, bolja točnost"},
    },
    "dgx": {
        "chat": {"model": "qwen2.5:72b", "note": "vrhunski chat model"},
        "embed": {"model": "bge-m3", "note": "višejezično ugrađivanje (dobro za HR)"},
        "utility": {"model": "qwen2.5:32b", "note": "veliki model za pomoćne zadatke"},
        "vlm": {"model": "llava:34b", "note": "vrhunski vizualni model"},
    },
}

_TIER_FLOORS = [(64, "dgx"), (32, "large"), (16, "medium"), (8, "small")]


def classify_tier(total_gb: float) -> str:
    for floor, tier in _TIER_FLOORS:
        if total_gb >= floor:
            return tier
    return "tiny"


_VRAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*GB", re.IGNORECASE)


def _gpu_vram_gb(gpu: str | None) -> float:
    if not gpu:
        return 0.0
    m = _VRAM_RE.search(gpu)
    return float(m.group(1)) if m else 0.0


def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _already_pulled(ollama_url: str) -> list[str]:
    try:
        with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=1) as resp:
            import json
            data = json.loads(resp.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def recommend(hw: dict | None = None, ollama_url: str = "http://127.0.0.1:11434") -> dict:
    if hw is None:
        from ragspine.ops import setup
        hw = setup.detect_hw()
    ram_gb = hw.get("ram_gb", 0) or 0
    total_gb = ram_gb + _gpu_vram_gb(hw.get("gpu"))
    tier = classify_tier(total_gb)
    return {
        "tier": tier,
        "total_gb": total_gb,
        "roles": {role: dict(rec) for role, rec in TIERS[tier].items()},
        "ollama_installed": _ollama_installed(),
        "already_pulled": _already_pulled(ollama_url),
    }


def litellm_config(recommendation: dict, ollama_url: str = "http://127.0.0.1:11434") -> str:
    lines = ["model_list:"]
    for role, rec in recommendation["roles"].items():
        model = rec.get("model")
        if not model:
            continue
        lines += [
            f"  - model_name: {role}",
            "    litellm_params:",
            f"      model: ollama/{model}",
            f"      api_base: {ollama_url}",
        ]
    return "\n".join(lines) + "\n"


def pull_commands(recommendation: dict) -> list[str]:
    seen: list[str] = []
    for rec in recommendation["roles"].values():
        model = rec.get("model")
        if model and model not in seen:
            seen.append(model)
    return [f"ollama pull {model}" for model in seen]


def report(hw: dict | None = None) -> str:
    rec = recommend(hw)
    lines = [
        "=== RAGSPINE preporuka modela ===",
        "",
        f"Tier hardvera: {rec['tier']} (ukupno {rec['total_gb']} GB RAM+VRAM)",
        "",
        "Preporuke po ulozi:",
    ]
    for role, r in rec["roles"].items():
        if "model" in r:
            lines.append(f"  {role}: {r['model']} — {r['note']}")
        else:
            lines.append(f"  {role}: nedostupno — {r['warn']}")
    lines += [
        "",
        f"Ollama instaliran: {'da' if rec['ollama_installed'] else 'ne'}",
        f"Već povučeni modeli: {', '.join(rec['already_pulled']) or 'nema/nedostupno'}",
        "",
        "Komande za povlačenje modela:",
    ]
    lines += [f"  {c}" for c in pull_commands(rec)] or ["  (nema — svi modeli nedostupni na ovom hardveru)"]
    lines += [
        "",
        "Napomena: ovo su preporuke, ne automatski download — operater bira i",
        "pokreće `ollama pull`. Spreman LiteLLM model_list: dobijete pozivom",
        "model_recommender.litellm_config(recommend()) ili GET /models/litellm.",
    ]
    return "\n".join(lines)
