"""Detekcija hardvera (za preflight/model_recommender). Stari setup-report je zamijenio wizard (ragspine setup)."""
import os
import platform
import shutil
from pathlib import Path

from ragspine.core.subproc import run_isolated


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
