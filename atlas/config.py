"""Configuration management for ATLAS."""
from dataclasses import dataclass
import os, secrets
from pathlib import Path


def _home() -> str:
    return os.path.expanduser("~")


def _env(name: str, default: str = "") -> str:
    """ATLAS_<name> primarno; RAGSPINE_<name> je trajni compat alias."""
    v = os.environ.get(f"ATLAS_{name}")
    if v is None:
        v = os.environ.get(f"RAGSPINE_{name}")  # compat: ragspine env alias
    return default if v is None else v


def default_data_dir() -> str:
    """~/.atlas; ako ne postoji a stari ~/.ragspine (compat) postoji — koristi stari."""
    new = os.path.join(_home(), ".atlas")
    legacy = os.path.join(_home(), ".ragspine")  # compat: ragspine data dir
    if not os.path.exists(new) and os.path.isdir(legacy):
        return legacy
    return new


@dataclass
class Config:
    data_dir: str
    db_path: str
    host: str
    port: int
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_provider: str
    anthropic_base_url: str
    ollama_url: str
    ocr_url: str
    ocr_langs: str
    embed_model: str
    nas_root: str
    imap_host: str
    imap_user: str
    imap_pass: str
    jwt_secret: str
    redact_pii: bool
    https_only: bool
    egress_allow: list[str]
    apprise_urls: list[str]
    mount_roots: list[str]
    digest_hour: int
    llm_path: str = ""  # OpenAI-kompat put iza base_url (B10); "" = /v1/chat/completions

    @classmethod
    def from_env(cls) -> "Config":
        e = _env
        data_dir = os.path.normpath(os.path.expanduser(e("DATA_DIR") or default_data_dir()))
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        # data_dir drži DB (PII), secret i modele — 0700 (no-op na Windowsu)
        try:
            os.chmod(data_dir, 0o700)
        except OSError:
            pass
        secret = e("JWT_SECRET", "")
        if not secret:
            sf = Path(data_dir) / "secret"
            if sf.exists():
                secret = sf.read_text().strip()
            else:
                secret = secrets.token_hex(32)
                sf.touch(mode=0o600)
                sf.write_text(secret)
        default_db = Path(data_dir) / "atlas.db"
        legacy_db = Path(data_dir) / "ragspine.db"  # compat: ragspine db ime
        if not default_db.exists() and legacy_db.exists():
            default_db = legacy_db
        return cls(
            data_dir=data_dir,
            db_path=e("DB_PATH", str(default_db)),
            host=e("HOST", "127.0.0.1"), port=int(e("PORT", "8400")),
            llm_base_url=e("LLM_BASE_URL", ""), llm_api_key=e("LLM_API_KEY", ""),
            llm_model=e("LLM_MODEL", ""),
            llm_provider=e("LLM_PROVIDER", ""),
            llm_path=e("LLM_PATH", ""),  # B10: env-only put uz LLM_BASE_URL (npr. Gemini)
            anthropic_base_url=e("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            ollama_url=e("OLLAMA_URL", "http://127.0.0.1:11434"),
            # Default = mali multilingual (220MB, dim 384, hrvatski OK) — pouzdano
            # se skida i na slabijoj mreži. Za bolju kvalitetu na jačem hardveru:
            # ATLAS_EMBED_MODEL=intfloat/multilingual-e5-large (2.24GB, dim 1024).
            # embed kod je model-agnostičan (dim iz modela, e5-prefiks uvjetno).
            ocr_url=e("OCR_URL", ""),
            ocr_langs=e("OCR_LANGS", "hrv+eng"),
            embed_model=e("EMBED_MODEL",
                          "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
            nas_root=e("NAS_ROOT", ""), imap_host=e("IMAP_HOST", ""),
            imap_user=e("IMAP_USER", ""), imap_pass=e("IMAP_PASS", ""),
            jwt_secret=secret, redact_pii=e("REDACT_PII", "0") == "1",
            https_only=e("HTTPS_ONLY", "0") == "1",
            egress_allow=[h for h in e("EGRESS_ALLOW", "").split(",") if h],
            apprise_urls=[u for u in e("APPRISE_URLS", "").split(",") if u],
            # Dozvoljeni korijeni mrežnih mapa (SMB mount točke); samo mape ispod
            # ovih smiju se registrirati/čitati. realpath da simlink ne zaobiđe scoping.
            mount_roots=[os.path.realpath(os.path.expanduser(p))
                         for p in e("MOUNT_ROOTS", "").split(",") if p.strip()],
            digest_hour=int(e("DIGEST_HOUR", "7")))

_cfg: Config | None = None
def get_config() -> Config:
    global _cfg
    if _cfg is None: _cfg = Config.from_env()
    return _cfg
def set_config(cfg: Config | None):
    global _cfg; _cfg = cfg
