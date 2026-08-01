"""Setup wizard: seed DB, detect hardware/LLM providers, report what's missing."""
import json
import os
import platform
import shutil
import urllib.request
from datetime import date
from pathlib import Path

from ragspine.core import optional
from ragspine.core.llm import _ollama_alive, load_oauth_token
from ragspine.core.spine import get_spine
from ragspine.core.subproc import run_isolated
from ragspine.ops import seeds

ENV_KEY_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "RAGSPINE_LLM_API_KEY")


def _year() -> int:
    return date.today().year


def _ram_gb() -> float:
    path = "/proc/meminfo"
    if not os.path.exists(path):
        return 0.0
    with open(path) as f:
        for line in f:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024 / 1024
    return 0.0


def detect_hw() -> dict:
    gpu = None
    try:
        rc, out, _err = run_isolated(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=5)
        if rc == 0 and out.strip():
            gpu = out.strip().splitlines()[0]
    except Exception:
        gpu = None
    return {
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": round(_ram_gb(), 2),
        "disk_free_gb": round(shutil.disk_usage(Path.home()).free / 1e9, 2),
        "gpu": gpu,
        "apple_silicon": platform.machine() == "arm64" and platform.system() == "Darwin",
    }


def llmfit(cfg) -> dict | None:
    if not shutil.which("llmfit"):
        return None
    try:
        rc, out, _err = run_isolated(["llmfit", "--json"], timeout=30)
        if rc != 0:
            return None
        return json.loads(out)
    except Exception:
        return None


def detect_providers(cfg) -> dict:
    env_keys = [name for name in ENV_KEY_NAMES if os.environ.get(name)]
    oauth_tok = load_oauth_token()
    oauth = [oauth_tok[0]] if oauth_tok else []
    ollama_models: list[str] = []
    if _ollama_alive(cfg):
        try:
            with urllib.request.urlopen(f"{cfg.ollama_url}/api/tags", timeout=2) as resp:
                data = json.loads(resp.read())
            ollama_models = [m["name"] for m in data.get("models", [])]
        except Exception:
            ollama_models = []
    return {"env_keys": env_keys, "oauth": oauth, "ollama_models": ollama_models}


def run(cfg) -> str:
    counts = seeds.all(get_spine(), _year())
    hw = detect_hw()
    fit = llmfit(cfg)
    providers = detect_providers(cfg)
    miss = optional.missing()

    lines = [
        "=== RAGSPINE setup ===",
        "",
        "Baza i sjemenke:",
        f"  kontni plan: {counts['kontni_plan']} redaka",
        f"  watch izvori: {counts['watch']}",
        f"  quickref: {counts['quickref']}",
        f"  kalendar rokova: {counts['kalendar']}",
        f"  dnevnice: {counts['dnevnice']}",
        "",
        "Hardver:",
        f"  CPU jezgre: {hw['cpu_cores']}",
        f"  RAM: {hw['ram_gb']} GB",
        f"  disk slobodno: {hw['disk_free_gb']} GB",
        f"  GPU: {hw['gpu'] or 'nema'}",
        f"  Apple Silicon: {'da' if hw['apple_silicon'] else 'ne'}",
        f"  llmfit preporuka: {fit if fit is not None else 'llmfit nije instaliran'}",
        "",
        "LLM provideri:",
        f"  env ključevi postavljeni: {', '.join(providers['env_keys']) or 'nijedan'}",
        f"  OAuth: {', '.join(providers['oauth']) or 'nema'}",
        f"  Ollama modeli: {', '.join(providers['ollama_models']) or 'nedostupno/nema'}",
        "",
        "Nedostaje:",
    ]
    if miss:
        lines += [f"  {k}: {v}" for k, v in miss.items()]
    else:
        lines.append("  ništa — sve opcionalne ovisnosti instalirane")
    if fit is None and shutil.which("llmfit") is None:
        lines.append("  llmfit: pip install llmfit (za preporuku modela po hardveru)")

    try:
        from ragspine.ops import model_recommender
        rec = model_recommender.recommend(hw)
        chat = rec["roles"].get("chat", {})
        chat_desc = chat.get("model") or f"nedostupno ({chat.get('warn')})"
        lines += [
            "",
            f"Preporuka modela (tier {rec['tier']}): chat={chat_desc} — `ragspine models` za sve uloge",
        ]
    except Exception:
        pass

    return "\n".join(lines)
