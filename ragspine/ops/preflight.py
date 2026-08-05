"""Preflight: stanje računala + preduvjeti za pokretanje RAGSPINE + kompresija-
svjestan izbor lokalnog modela ("koji LLM stane, na kojoj kvantizaciji").

Tri javna ulaza:
- system_state(): RAM/disk/CPU/GPU/OS/Python (cross-OS, čisti stdlib).
- requirements(): lista "što treba na računalu" sa statusom ok/warn/fail + fix.
- model_fits(): za svaki model iz kataloga, koja kvantizacija stane na ovo
  računalo (fit-pill), po uzoru na Jan/GPT4All (usporedba veličine kvantiziranog
  modela sa slobodnom memorijom, uz sigurnosnu marginu).

ponytail: katalog i veličine su kurirani (ručno, kao GPT4All ramrequired), ne
skinu se metapodaci s mreže. Upgrade path: povuci žive veličine s Ollama/HF
registryja kad zatreba.
"""
import os
import shutil
import sys

# --- stanje računala (cross-OS, bez nove ovisnosti) ---


_GIB = 1024 ** 3


def _mem_gb() -> tuple[float, float]:
    """(ukupno_GiB, slobodno_GiB) best-effort, ujednačene jedinice (GiB svugdje).
    psutil ako postoji, inače OS-specifično; macOS bez psutila = ukupno preko
    sysconfa (slobodno nepoznato → vraća ukupno, jer fit ionako računa po ukupnom)."""
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
            if ok:  # BOOL: 0 = neuspjeh
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
        total = info["MemTotal"] / 1024 / 1024  # kB → GiB
        avail = info.get("MemAvailable", info.get("MemFree", 0)) / 1024 / 1024
        return total, avail
    except Exception:
        pass
    try:  # macOS / generički POSIX — ukupno preko sysconfa
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / _GIB
        return total, total
    except Exception:
        return 0.0, 0.0


def _vram_gb() -> float:
    """VRAM iz nvidia-smi memory.total (parsanje naziva GPU-a je nepouzdano —
    Codex nalaz). 0 ako nema NVIDIA GPU-a."""
    try:
        from ragspine.core.subproc import run_isolated
        rc, out, _err = run_isolated(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], timeout=5)
        if rc == 0 and out.strip():
            return float(out.strip().splitlines()[0]) / 1024  # MiB → GiB
    except Exception:
        pass
    return 0.0


def system_state(cfg=None) -> dict:
    from ragspine.ops import setup
    hw = setup.detect_hw()
    total_ram, free_ram = _mem_gb()
    data_dir = getattr(cfg, "data_dir", None) or os.path.expanduser("~/.ragspine")
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
    }


# --- preduvjeti: "što treba na računalu da RAGSPINE radi" ---

# (modul za import, ljudski naziv, pip/winget uputa ako fali)
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


def requirements(cfg=None) -> list[dict]:
    """Lista preduvjeta sa statusom. 'fail' = RAGSPINE neće ispravno raditi;
    'warn' = radi degradirano; 'ok' = spremno."""
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

    data_dir = getattr(cfg, "data_dir", None) or os.path.expanduser("~/.ragspine")
    # stvarni upis+brisanje (os.access W_OK je nepouzdan na Windows ACL — Codex)
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

    tess = shutil.which("tesseract")
    langs_ok = False
    if tess:
        try:
            from ragspine.core.subproc import run_isolated
            rc, tout, terr = run_isolated(["tesseract", "--list-langs"], timeout=5)
            blob = f"{tout}\n{terr}".lower()
            langs_ok = "hrv" in blob and "eng" in blob
        except Exception:
            langs_ok = False
    tdetail = ("hrv+eng dostupni" if langs_ok else "instaliran bez hrv/eng jezika") if tess else "nije pronađen"
    out.append({"key": "tesseract", "naziv": "OCR (Tesseract, hrv+eng)",
                "status": _status(bool(tess) and langs_ok, warn=True), "detalj": tdetail,
                "fix": "winget install UB-Mannheim.TesseractOCR (+ hrv i eng jezični paket)"})

    for mod, naziv, fix in _OPTIONAL_MODULES:
        try:
            importlib.import_module(mod)
            present = True
        except Exception:
            present = False
        out.append({"key": mod, "naziv": naziv, "status": _status(present, warn=True),
                    "detalj": "instalirano" if present else "nedostaje", "fix": fix})

    return out


# --- kompresija-svjestan izbor modela ---

# Kurirani katalog lokalnih modela. `quants` = veličina GGUF datoteke (GB) po
# kvantizaciji; ista arhitektura, različita kompresija → različit memorijski
# otisak. Brojevi su približni (kao GPT4All ramrequired), ne skidaju se s mreže.
MODEL_CATALOG = [
    {"name": "qwen2.5:3b", "role": "chat", "params": "3B",
     "quants": {"Q4_K_M": 2.0, "Q5_K_M": 2.3, "Q8_0": 3.3, "fp16": 6.2}},
    {"name": "llama3.2:3b", "role": "chat", "params": "3B",
     "quants": {"Q4_K_M": 2.0, "Q5_K_M": 2.3, "Q8_0": 3.4, "fp16": 6.4}},
    {"name": "qwen2.5:7b", "role": "chat", "params": "7B",
     "quants": {"Q4_K_M": 4.7, "Q5_K_M": 5.4, "Q8_0": 8.1, "fp16": 15.2}},
    {"name": "llama3.1:8b", "role": "chat", "params": "8B",
     "quants": {"Q4_K_M": 4.9, "Q5_K_M": 5.7, "Q8_0": 8.5, "fp16": 16.1}},
    {"name": "qwen2.5:14b", "role": "chat", "params": "14B",
     "quants": {"Q4_K_M": 9.0, "Q5_K_M": 10.5, "Q8_0": 15.7, "fp16": 29.5}},
    {"name": "qwen2.5:32b", "role": "chat", "params": "32B",
     "quants": {"Q4_K_M": 19.9, "Q5_K_M": 23.3, "Q8_0": 34.8, "fp16": 65.5}},
    {"name": "bge-m3", "role": "embed", "params": "0.6B",
     "quants": {"Q4_K_M": 0.4, "fp16": 1.2}},
    {"name": "nomic-embed-text", "role": "embed", "params": "0.1B",
     "quants": {"Q4_K_M": 0.1, "fp16": 0.3}},
]

# Jedna jasna margina (Codex): udio UKUPNOG RAM-a koji model smije zauzeti.
# <50% = komotno stane · 50–70% = tijesno (radi, ali malo zraka za KV cache/OS)
# ≥70% = ne. Ukupni (ne trenutno slobodni) RAM = sposobnost namjenskog servera.
_FITS_FRAC = 0.5
_TIGHT_FRAC = 0.7
_VRAM_RESERVE = 0.8  # KV cache/runtime rezerva na GPU-u


def fit_pill(size_gb: float, total_gb: float) -> str:
    if total_gb <= 0:
        return "unknown"
    frac = size_gb / total_gb
    if frac < _FITS_FRAC:
        return "fits"
    if frac < _TIGHT_FRAC:
        return "tight"
    return "too_big"


def model_fits(cfg=None, state: dict | None = None) -> list[dict]:
    """Za svaki model: fit-pill po kvantizaciji. best_quant = najkvalitetnija koja
    KOMOTNO stane (fits); tight_quant = najkvalitetnija koja barem tijesno stane.
    Budžet = UKUPNI RAM (sposobnost). VRAM ≥ veličina+rezerva → može na GPU (brže)."""
    st = state or system_state(cfg)
    total = st.get("ram_total_gb") or st.get("ram_free_gb") or 0.0
    vram = st.get("vram_gb") or 0.0
    quality_order = ["fp16", "Q8_0", "Q5_K_M", "Q4_K_M"]  # najkvalitetnija → najmanja
    out = []
    for m in MODEL_CATALOG:
        quants = []
        best_fit = None
        tight_fit = None
        for q in [x for x in quality_order if x in m["quants"]]:
            size = m["quants"][q]
            pill = fit_pill(size, total)
            gpu = vram > 0 and size <= vram * _VRAM_RESERVE
            quants.append({"quant": q, "size_gb": size, "pill": pill, "gpu_ready": gpu})
            if pill == "fits" and best_fit is None:
                best_fit = q
            if pill == "tight" and tight_fit is None:
                tight_fit = q
        out.append({"name": m["name"], "role": m["role"], "params": m["params"],
                    "quants": quants, "best_quant": best_fit, "tight_quant": tight_fit,
                    "installable": best_fit is not None or tight_fit is not None})
    return out


def _llmfit(cfg) -> dict | None:
    """Vanjski llmfit CLI ako je instaliran (dodatna preporuka po hardveru).
    None ako ga nema — naš model_fits ionako pokriva izbor."""
    from ragspine.ops import setup
    try:
        return setup.llmfit(cfg)
    except Exception:
        return None


def _redact(st: dict, reqs: list) -> tuple[dict, list]:
    """Za anonimni onboarding: makni točne putanje / OS string / naziv GPU-a koje
    bi LAN promatrač mogao skupljati (Codex). Brojevi (RAM/disk/fit) ostaju."""
    st = {**st, "os": None, "gpu": None}
    out = []
    for r in reqs:
        rr = dict(r)
        if r["key"] == "data_dir":
            rr["detalj"] = "podatkovna mapa" if r["status"] == "ok" else "nije upisiva"
        out.append(rr)
    return st, out


def summary(cfg=None, reduced: bool = False) -> dict:
    """Sve na jednom mjestu za Postavke ekran / wizard. reduced=True za anonimni
    onboarding (redigirane putanje/inventar)."""
    st = system_state(cfg)
    reqs = requirements(cfg)
    if reduced:
        st, reqs = _redact(st, reqs)
    from ragspine.ops import model_recommender
    try:
        # proslijedi NAŠ RAM (Codex: stari detect_hw vidi RAM=0 na Windowsu → tier tiny)
        hw = {"ram_gb": st["ram_total_gb"], "gpu": st.get("gpu")}
        rec = model_recommender.recommend(hw, ollama_url=getattr(cfg, "ollama_url",
                                                                 "http://127.0.0.1:11434"))
    except Exception:
        rec = None
    return {
        "state": st,
        "requirements": reqs,
        "requirements_ok": all(r["status"] != "fail" for r in reqs),
        "models": model_fits(cfg, st),
        "recommended_tier": (rec or {}).get("tier"),
        "ollama_installed": (rec or {}).get("ollama_installed", False),
        "already_pulled": (rec or {}).get("already_pulled", []),
        "llmfit": _llmfit(cfg),
    }
