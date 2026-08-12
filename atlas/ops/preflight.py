"""Preflight: machine state + prerequisites for running ATLAS.

Public functions:
- system_state(): RAM/disk/CPU/GPU/OS/Python (cross-OS, pure stdlib).
- requirements(): list of "what the machine needs" with status ok/warn/fail + fix.
- llmfit_models(): chat models from llmfit (hardware-sensitive quantization choice).
- fit_pill(): RAM fraction for the embedding model; embedding itself is hand-curated.
"""
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

from atlas import config
from atlas.core.subproc import run_isolated, run_streaming
from atlas.ops import winpath

# --- machine state (cross-OS, no new dependency) ---


_GIB = 1024 ** 3


def _mem_gb() -> tuple[float, float]:
    """(total_GiB, free_GiB) best-effort, consistent units (GiB everywhere).
    psutil if present, otherwise OS-specific; macOS without psutil = total via
    sysconf (free unknown -> returns total, since fit computes by total anyway)."""
    try:
        import psutil  # optional
        vm = psutil.virtual_memory()
        return vm.total / _GIB, vm.available / _GIB
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            if ok:  # BOOL: 0 = failure
                return m.ullTotalPhys / _GIB, m.ullAvailPhys / _GIB
        except Exception:
            pass
        return 0.0, 0.0
    try:  # Linux
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                if v:
                    info[k.strip()] = int(v.split()[0])  # kB
        total = info["MemTotal"] / 1024 / 1024  # kB -> GiB
        avail = info.get("MemAvailable", info.get("MemFree", 0)) / 1024 / 1024
        return total, avail
    except Exception:
        pass
    try:  # macOS / generic POSIX -- total via sysconf
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / _GIB
        return total, total
    except Exception:
        return 0.0, 0.0


def _vram_gb() -> float:
    """VRAM from nvidia-smi memory.total (parsing the GPU name is unreliable --
    Codex finding). 0 if there is no NVIDIA GPU."""
    try:
        rc, out, _err = run_isolated(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], timeout=5)
        if rc == 0 and out.strip():
            return float(out.strip().splitlines()[0]) / 1024  # MiB -> GiB
    except Exception:
        pass
    return 0.0


def system_state(cfg=None) -> dict:
    from atlas.ops import setup
    hw = setup.detect_hw()
    total_ram, free_ram = _mem_gb()
    data_dir = getattr(cfg, "data_dir", None) or config.default_data_dir()
    try:
        disk_free = shutil.disk_usage(data_dir if os.path.isdir(data_dir) else os.path.expanduser("~")).free / 1e9
    except OSError:
        disk_free = 0.0
    return {
        "os": f"{os.name} / {sys.platform}",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "cpu_cores": hw.get("cpu_cores"),
        "ram_total_gb": round(total_ram, 1),
        "ram_free_gb": round(free_ram, 1),
        "disk_free_gb": round(disk_free, 1),
        "gpu": hw.get("gpu"),
        "vram_gb": round(_vram_gb(), 1),
        "apple_silicon": hw.get("apple_silicon", False),
        "ip_mode": _ip_mode(),
    }


# --- prerequisites: "what the machine needs for ATLAS to work" ---

# (module to import, human-readable name, pip/winget instruction if missing)
_OPTIONAL_MODULES = [
    ("fitz", "Čitanje PDF-ova (PyMuPDF)", "pip install pymupdf"),
    ("fastembed", "Semantička pretraga (embeddings)", "pip install fastembed"),
    ("sqlite_vec", "Vektorski indeks", "pip install sqlite-vec"),
    ("openpyxl", "Excel izvoz", "pip install openpyxl"),
    ("docx", "Word dokumenti", "pip install python-docx"),
    ("apprise", "Slanje obavijesti/poruka", "pip install apprise"),
]

_MIN_DISK_GB = 5.0
_MIN_RAM_GB = 4.0


def _status(ok: bool, warn: bool = False) -> str:
    return "ok" if ok else ("warn" if warn else "fail")


def ollama_version(url: str = "http://127.0.0.1:11434") -> str | None:
    """GET /api/version -> "0.5.4" or None. Does not raise."""
    import json
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=3) as r:
            return json.loads(r.read()).get("version") or None
    except Exception:
        return None


_OLLAMA_FLOOR = "0.5.0"


def ollama_floor_ok(version: str | None, floor: str = _OLLAMA_FLOOR) -> bool:
    """Tuple comparison "0.5.4" >= "0.5.0". Unparseable/None -> False."""
    if not version:
        return False
    try:
        v = tuple(int(x) for x in version.split("-")[0].split("."))
        f = tuple(int(x) for x in floor.split("."))
        return v >= f
    except ValueError:
        return False


def start_ollama(wait_s: float = 8.0, url: str = "http://127.0.0.1:11434") -> bool:
    """Try to start `ollama serve` detached, then wait until /api/tags responds.
    False when the binary does not exist or the service does not respond within wait_s."""
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        # POSIX start_new_session is a no-op on Windows -- DETACHED_PROCESS is
        # needed so the daemon survives closing the wizard console. getattr
        # fallback so a test on Linux with mocked platform.system()=="Windows"
        # does not fail with AttributeError (the attributes exist only on the
        # Windows build of Python).
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except OSError:
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        ok, _ = ollama_ready(url)
        if ok:
            return True
        time.sleep(0.25)
    ok, _ = ollama_ready(url)
    return ok


def ollama_ready(url: str = "http://localhost:11434") -> tuple[bool, str]:
    """200 on /api/tags = the service is running. Does not rely on `ollama --version`
    (hermes: installed but not started is a common case)."""
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=3) as r:
            return (r.status == 200, "servis radi")
    except Exception:
        return (False, "nije dostupna (servis ne radi ili nije instalirana)")


def ollama_pull(name: str, url: str = "http://127.0.0.1:11434", *, out=print) -> bool:
    """Download a model via the Ollama daemon (POST /api/pull, NDJSON stream).
    The daemon resumes a partial download on its own -- calling again is enough.
    True on the final "success" status."""
    import json
    req = urllib.request.Request(f"{url}/api/pull",
                                 data=json.dumps({"model": name}).encode(),
                                 headers={"Content-Type": "application/json"})
    last_pct = -1
    stream = out is print and sys.stdout.isatty()
    step = 1 if stream else 10
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            for raw in r:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("error"):
                    out(f"Greška pri skidanju: {ev['error']}")
                    return False
                status = ev.get("status", "")
                total, done = ev.get("total"), ev.get("completed")
                if total and done is not None:
                    pct = int(done * 100 / total)
                    if pct != last_pct and (pct % step == 0 or pct == 100):
                        line = f"  {status}: {pct}%"
                        if stream:
                            sys.stdout.write("\r" + line + "   ")
                            sys.stdout.flush()
                        else:
                            out(line)
                        last_pct = pct
                elif status:
                    if stream and last_pct >= 0:
                        sys.stdout.write("\n")
                        last_pct = -1
                    out(f"  {status}")
                if status == "success":
                    if stream and last_pct >= 0:
                        sys.stdout.write("\n")
                    return True
    except Exception as e:
        out(f"Greška pri skidanju modela: {e}")
        return False
    out("Skidanje prekinuto prije kraja — pokreni ponovno (nastavlja gdje je stalo).")
    return False


def quant_tags(ollama_name: str, quant: str) -> list[str]:
    """Candidate tags with the EXACT quantization (E2E BUG: a bare tag pulls
    the registry default -- typically larger than the llmfit estimate).
    Conventions vary, hence the list: '<name>-instruct-<q>' then '<name>-<q>';
    the caller adds the bare tag as the last fallback."""
    if not quant or ":" not in ollama_name:
        return []
    q = quant[0].lower() + quant[1:]   # Q4_K_M -> q4_K_M (ollama style)
    return [f"{ollama_name}-instruct-{q}", f"{ollama_name}-{q}"]


def ollama_model_size(tag: str, url: str = "http://127.0.0.1:11434") -> float:
    """Actual size of the downloaded model in GB (GET /api/tags); 0.0 =
    unknown/error -- the caller then skips the comparison."""
    import json
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=10) as r:
            data = json.loads(r.read())
        for m in data.get("models", []):
            if m.get("name") == tag:
                return round(float(m.get("size", 0)) / 1e9, 1)
    except Exception:
        pass
    return 0.0


_LLMFIT_CHAT_CATS = {"Chat", "Coding", "Reasoning", "General"}
_LLMFIT_MAX_ROWS = 12

# The llmfit subprocess is slow (~seconds); the wizard and web preflight call
# it multiple times in a process. Cached per process; None (failure) is NOT
# cached so a retry-after-install works. Tests reset it via conftest.
# Note: a successful-but-empty result (llmfit works, 0 models pass the filter)
# is cached forever -- harmless, because the retry branch in the wizard looks at
# PATH (llmfit availability), not the list contents.
_llmfit_cache: list[dict] | None = None


def llmfit_models(cfg=None) -> list[dict] | None:
    """Models per llmfit: it detects the hardware and computes the best quantization.
    We return only Ollama-runnable chat models that fit (Good/Marginal),
    sorted by score. None when llmfit is unavailable or its output is invalid."""
    global _llmfit_cache
    if _llmfit_cache is not None:
        return _llmfit_cache
    import json
    try:
        # PATH binary first; the `python -m llmfit` wrapper looks for the binary in the sysconfig scripts path and breaks for pip --user installs.
        exe = shutil.which("llmfit")
        cmd = [exe, "--json"] if exe else [sys.executable, "-m", "llmfit", "--json"]
        # Total RAM (dedicated-server capability), NOT currently free --
        # otherwise llmfit sizes by free memory, so on a loaded machine
        # (cache, other processes) it sees every model as "Too Tight" and the
        # list is empty (Finding #1, P2b review). --ram override forces llmfit
        # to compute by total.
        total = system_state(cfg).get("ram_total_gb") or 0
        if total > 0:
            cmd = cmd + ["--ram", f"{round(total)}G"]
        # llmfit's Rust allocator occasionally crashes (SIGABRT) at the default
        # run_isolated limit of 512 MB address space -- measured ~50% failures
        # (non-deterministic, depends on heap layout); 1024 MB is reliable across
        # repeated trials and still far below the machine's actual RAM.
        rc, out, _err = run_isolated(cmd, timeout=60, mem_mb=1024)
        if rc != 0:
            return None
        data = json.loads(out)
    except Exception:
        return None
    rows = []
    for m in data.get("models", []):
        if not m.get("ollama_name"):
            continue
        if m.get("category") not in _LLMFIT_CHAT_CATS:
            continue
        if m.get("fit_label") not in ("Good", "Marginal"):
            continue
        rows.append({
            "name": m.get("name", ""), "ollama_name": m["ollama_name"],
            "category": m.get("category", ""), "fit_label": m["fit_label"],
            "best_quant": m.get("best_quant", ""),
            "memory_gb": float(m.get("memory_required_gb") or 0.0),
            "tps": float(m.get("estimated_tps") or 0.0),
            "score": float(m.get("score") or 0.0),
            "use_case": m.get("use_case") or "",
            "params": m.get("parameter_count") or "",
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    # Dedup by ollama_name: several HF variants can map to the same Ollama tag
    # (e.g. qwen2.5:0.5b, smollm2:135m twice in the top-12 -- Finding #2). The
    # list is already sorted by score descending, so the first occurrence = best score.
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        if r["ollama_name"] in seen:
            continue
        seen.add(r["ollama_name"])
        deduped.append(r)
    _llmfit_cache = deduped[:_LLMFIT_MAX_ROWS]
    return _llmfit_cache


def local_ip() -> str:
    """Primary LAN IP -- UDP connect trick (sends no packets). 127.0.0.1 on error."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def port_free(host: str, port: int) -> bool:
    """True when the port can be bound (free).
    SO_REUSEADDR has different semantics on Windows than on POSIX -- it allows
    binding to an already-used port, so the check would always pass (Codex finding).
    Windows: SO_EXCLUSIVEADDRUSE if present, otherwise no reuse option."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name != "nt":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            else:
                excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if excl is not None:
                    s.setsockopt(socket.SOL_SOCKET, excl, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def internet_ok(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Socket connect to a DNS server (Google) -- a quick internet availability check."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ip_mode() -> str:
    """Static address or DHCP -- detect OS-specifically.
    Windows: netsh; elsewhere: unknown (detection is LAN-specific)."""
    if platform.system() != "Windows":
        return "unknown"
    try:
        rc, out, _ = run_isolated(["netsh", "interface", "ip", "show", "config"], timeout=5)
        for line in out.lower().splitlines():
            normalized = " ".join(line.split())
            if "dhcp enabled: yes" in normalized:
                return "dhcp"
            if "dhcp enabled: no" in normalized:
                return "static"
    except Exception:
        pass
    return "unknown"


def requirements(cfg=None) -> list[dict]:
    """List of prerequisites with status. 'fail' = ATLAS will not work correctly;
    'warn' = works degraded; 'ok' = ready."""
    import importlib

    st = system_state(cfg)
    out: list[dict] = []

    py_ok = sys.version_info[:2] >= (3, 11)
    out.append({"key": "python", "naziv": "Python 3.11+", "status": _status(py_ok),
                "detalj": st["python"], "fix": "instaliraj Python 3.11+ s python.org"})

    out.append({"key": "ram", "naziv": f"RAM ≥ {_MIN_RAM_GB:.0f} GB",
                "status": _status(st["ram_total_gb"] >= _MIN_RAM_GB, warn=st["ram_total_gb"] >= 2),
                "detalj": f"{st['ram_total_gb']} GB (slobodno {st['ram_free_gb']} GB)", "fix": "dodaj RAM"})

    out.append({"key": "disk", "naziv": f"Slobodan disk ≥ {_MIN_DISK_GB:.0f} GB",
                "status": _status(st["disk_free_gb"] >= _MIN_DISK_GB, warn=st["disk_free_gb"] >= 1),
                "detalj": f"{st['disk_free_gb']} GB", "fix": "oslobodi prostor (modeli+indeks trebaju mjesta)"})

    data_dir = getattr(cfg, "data_dir", None) or config.default_data_dir()
    # actual write+delete (os.access W_OK is unreliable on Windows ACL -- Codex)
    writable = False
    if os.path.isdir(data_dir):
        probe = os.path.join(data_dir, ".rs_write_probe")
        try:
            with open(probe, "w") as f:
                f.write("x")
            os.unlink(probe)
            writable = True
        except OSError:
            writable = False
    out.append({"key": "data_dir", "naziv": "Podatkovna mapa upisiva", "status": _status(writable),
                "detalj": data_dir, "fix": "provjeri dozvole nad podatkovnom mapom"})

    tess = winpath.find_binary("tesseract")
    langs_ok = False
    if tess:
        try:
            rc, tout, terr = run_isolated([tess, "--list-langs"], timeout=5)
            blob = f"{tout}\n{terr}".lower()
            langs_ok = "hrv" in blob and "eng" in blob
        except Exception:
            langs_ok = False
    tdetail = ("hrv+eng dostupni" if langs_ok else "instaliran bez hrv/eng jezika") if tess else "nije pronađen"
    out.append({"key": "tesseract", "naziv": "OCR (Tesseract, hrv+eng)",
                "status": _status(bool(tess) and langs_ok), "detalj": tdetail,
                "fix": "winget install UB-Mannheim.TesseractOCR (+ hrv i eng jezični paket)"})

    ok, odetail = ollama_ready(getattr(cfg, "ollama_url", "http://localhost:11434"))
    out.append({"key": "ollama", "naziv": "Ollama (pokretač lokalnog LLM-a)",
                "status": _status(ok, warn=True), "detalj": odetail,
                "fix": "winget install Ollama.Ollama pa pokreni 'ollama serve'"})

    inet_ok = internet_ok()
    out.append({"key": "internet", "naziv": "Internet (za skidanje modela/instalacije)",
                "status": _status(inet_ok, warn=True),
                "detalj": "dostupan" if inet_ok else "nema — radi offline s onim što ima",
                "fix": "spoji mrežu ili koristi --offline s ručno skinutim modelima"})

    import warnings
    for mod, name, fix in _OPTIONAL_MODULES:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")   # fitz deprecation etc. not into output
                importlib.import_module(mod)
            present = True
        except Exception:
            present = False
        out.append({"key": mod, "naziv": name, "status": _status(present, warn=True),
                    "detalj": "instalirano" if present else "nedostaje", "fix": fix})

    return out


# --- compression-aware model choice (embedding) ---

# One clear margin (Codex): fraction of TOTAL RAM the model is allowed to take.
# <50% = fits comfortably . 50-70% = tight (works, but little room for KV cache/OS)
# >=70% = no. Total (not currently free) RAM = dedicated-server capability.
_FITS_FRAC = 0.5
_TIGHT_FRAC = 0.7


def fit_pill(size_gb: float, total_gb: float) -> str:
    if total_gb <= 0:
        return "unknown"
    frac = size_gb / total_gb
    if frac < _FITS_FRAC:
        return "fits"
    if frac < _TIGHT_FRAC:
        return "tight"
    return "too_big"


def _redact(st: dict, reqs: list) -> tuple[dict, list]:
    """For anonymous onboarding: remove exact paths / OS string / GPU name that
    a LAN observer could collect (Codex). Numbers (RAM/disk/fit) stay."""
    st = {**st, "os": None, "gpu": None}
    out = []
    for r in reqs:
        rr = dict(r)
        if r["key"] == "data_dir":
            rr["detalj"] = "podatkovna mapa" if r["status"] == "ok" else "nije upisiva"
        out.append(rr)
    return st, out


def summary(cfg=None, reduced: bool = False) -> dict:
    """Everything in one place for the Settings screen / wizard. reduced=True for
    anonymous onboarding (redacted paths/inventory). Models come from llmfit_models()."""
    st = system_state(cfg)
    reqs = requirements(cfg)
    if reduced:
        st, reqs = _redact(st, reqs)
    from atlas.ops import model_recommender
    try:
        # pass OUR RAM (Codex: old detect_hw sees RAM=0 on Windows -> tier tiny)
        hw = {"ram_gb": st["ram_total_gb"], "gpu": st.get("gpu")}
        rec = model_recommender.recommend(hw, ollama_url=getattr(cfg, "ollama_url",
                                                                 "http://127.0.0.1:11434"))
    except Exception:
        rec = None
    return {
        "state": st,
        "requirements": reqs,
        "requirements_ok": all(r["status"] != "fail" for r in reqs),
        "recommended_tier": (rec or {}).get("tier"),
        "ollama_installed": (rec or {}).get("ollama_installed", False),
        "already_pulled": (rec or {}).get("already_pulled", []),
        "models": llmfit_models(cfg) or [],  # llmfit as the model source (if missing -> [])
    }


# --- Windows auto-install + llmfit + proxy ---

WINGET_IDS = {"tesseract": "UB-Mannheim.TesseractOCR", "ollama": "Ollama.Ollama"}
_WINGET_BIN = {"tesseract": "tesseract", "ollama": "ollama"}
_WINGET_INFO = {"ollama": "~700 MB, tipično nekoliko minuta",
                "tesseract": "~60 MB, ispod minute"}
_TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/{lang}.traineddata"


def _dir_writable(d) -> bool:
    """Actual write probe (os.access is unreliable on Windows ACL)."""
    import pathlib
    d = pathlib.Path(d)
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".atlas_write_probe"
        probe.write_bytes(b"x")
        probe.unlink()
        return True
    except OSError:
        return False


def ensure_traineddata(langs=("hrv", "eng"), *, out=print,
                       urlopen=urllib.request.urlopen) -> bool:
    """Ensure Tesseract languages (E2E: the UB-Mannheim package lacks hrv).
    Target: tessdata next to the exe; without permission (Program Files without
    elevation) -> <data_dir>/tessdata + TESSDATA_PREFIX (process + persistent).
    TESSDATA_PREFIX is EXCLUSIVE -- languages the primary already has are also
    copied into the fallback dir, otherwise eng would disappear."""
    from pathlib import Path
    exe = winpath.find_binary("tesseract")
    if not exe:
        return False
    primary = Path(exe).parent / "tessdata"
    target = primary if _dir_writable(primary) else Path(config.default_data_dir()) / "tessdata"
    fallback_mode = target != primary
    if fallback_mode and not _dir_writable(target):
        out("  ✗ nijedna tessdata lokacija nije upisiva.")
        return False
    target.mkdir(parents=True, exist_ok=True)
    for lang in langs:
        fname = f"{lang}.traineddata"
        if (target / fname).exists():
            continue
        if fallback_mode and (primary / fname).exists():
            shutil.copy2(primary / fname, target / fname)
            continue
        out(f"  Skidam {fname} (~15 MB)...")
        try:
            with urlopen(_TESSDATA_URL.format(lang=lang), timeout=120) as r:
                data = r.read()
            (target / fname).write_bytes(data)
        except Exception as e:
            out(f"  ✗ {fname}: {e}")
            return False
        out(f"  ✓ {fname} → {target}")
    if fallback_mode:
        os.environ["TESSDATA_PREFIX"] = str(target)
        winpath.persist_user_env("TESSDATA_PREFIX", str(target))
        out(f"  TESSDATA_PREFIX postavljen: {target}")
    return True


def install_via_winget(key: str, *, out=print) -> bool:
    """Windows auto-install via the winget allowlist. 'Already installed' is
    detected and skipped (E2E finding); output goes LIVE (run_streaming);
    after install, PATH refresh from the registry + known locations instead of
    'restart the terminal'; tesseract also gets the hrv/eng languages."""
    if key not in WINGET_IDS:
        raise ValueError(f"nepoznat paket: {key!r}")
    wid = WINGET_IDS[key]
    cmd = ["winget", "install", "--exact", "--id", wid, "--source", "winget",
           "--accept-package-agreements", "--accept-source-agreements"]
    if platform.system() != "Windows":
        out(f"Auto-install je Windows-only. Ručno: {' '.join(cmd)}")
        out("  (Linux: apt/dnf; macOS: brew — potraži paket u svom package manageru.)")
        return False
    existing = winpath.find_binary(_WINGET_BIN[key])
    if existing:
        out(f"✓ {wid} je već instaliran ({existing}) — preskačem winget.")
    else:
        info = _WINGET_INFO.get(key, "")
        out(f"Instaliram {wid} ({info}) — očekuj UAC potvrdu (klikni Da); "
            "napredak ispod:")
        rc = run_streaming(cmd, timeout=900, out=out)
        if rc != 0:
            out(f"winget nije uspio (rc {rc}).")
            return False
        winpath.refresh_path_from_registry()
    exe = winpath.find_binary(_WINGET_BIN[key])
    if not exe:
        out(f"Instalirano, ali '{_WINGET_BIN[key]}' nije pronađen ni na "
            "poznatim lokacijama — provjeri instalaciju pa ponovi.")
        return False
    if not shutil.which(_WINGET_BIN[key]):
        exe_dir = os.path.dirname(exe)
        winpath.append_user_path(exe_dir)
        out(f"  PATH dopunjen: {exe_dir}")
    if key == "tesseract" and not ensure_traineddata(("hrv", "eng"), out=out):
        return False
    out(f"✓ {wid} spreman ({exe}).")
    return True


def install_llmfit(*, out=print) -> bool:
    """pip install llmfit (fallback when atlas is not installed with its deps)."""
    out("Instaliram llmfit (pip)...")
    rc, _o, err = run_isolated([sys.executable, "-m", "pip", "install", "--user", "llmfit"],
                               timeout=600)
    if rc != 0:
        out(f"pip nije uspio: {err[:200]}")
        return False
    ok = bool(shutil.which("llmfit"))
    out("✓ llmfit instaliran." if ok else "⚠ llmfit instaliran, ali nije na PATH-u.")
    return ok


def set_proxy(spine, proxy: str) -> None:
    spine.set_override("setup", "proxy", proxy or "")


def get_proxy(spine) -> str:
    return spine.get_override("setup", "proxy") or ""
