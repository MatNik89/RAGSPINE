"""Configuration management for RAGSPINE."""
from dataclasses import dataclass
import os, secrets
from pathlib import Path

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

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        data_dir = os.path.expanduser(e("RAGSPINE_DATA_DIR", "~/.ragspine"))
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        secret = e("RAGSPINE_JWT_SECRET", "")
        if not secret:
            sf = Path(data_dir) / "secret"
            if sf.exists():
                secret = sf.read_text().strip()
            else:
                secret = secrets.token_hex(32)
                sf.touch(mode=0o600)
                sf.write_text(secret)
        return cls(
            data_dir=data_dir,
            db_path=e("RAGSPINE_DB_PATH", str(Path(data_dir) / "ragspine.db")),
            host=e("RAGSPINE_HOST", "127.0.0.1"), port=int(e("RAGSPINE_PORT", "8400")),
            llm_base_url=e("RAGSPINE_LLM_BASE_URL", ""), llm_api_key=e("RAGSPINE_LLM_API_KEY", ""),
            llm_model=e("RAGSPINE_LLM_MODEL", ""),
            llm_provider=e("RAGSPINE_LLM_PROVIDER", ""),
            anthropic_base_url=e("RAGSPINE_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            ollama_url=e("RAGSPINE_OLLAMA_URL", "http://127.0.0.1:11434"),
            # Default = mali multilingual (220MB, dim 384, hrvatski OK) — pouzdano
            # se skida i na slabijoj mreži. Za bolju kvalitetu na jačem hardveru:
            # RAGSPINE_EMBED_MODEL=intfloat/multilingual-e5-large (2.24GB, dim 1024).
            # embed kod je model-agnostičan (dim iz modela, e5-prefiks uvjetno).
            ocr_url=e("RAGSPINE_OCR_URL", ""),
            embed_model=e("RAGSPINE_EMBED_MODEL",
                          "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
            nas_root=e("RAGSPINE_NAS_ROOT", ""), imap_host=e("RAGSPINE_IMAP_HOST", ""),
            imap_user=e("RAGSPINE_IMAP_USER", ""), imap_pass=e("RAGSPINE_IMAP_PASS", ""),
            jwt_secret=secret, redact_pii=e("RAGSPINE_REDACT_PII", "0") == "1",
            https_only=e("RAGSPINE_HTTPS_ONLY", "0") == "1",
            egress_allow=[h for h in e("RAGSPINE_EGRESS_ALLOW", "").split(",") if h],
            apprise_urls=[u for u in e("RAGSPINE_APPRISE_URLS", "").split(",") if u],
            # Dozvoljeni korijeni mrežnih mapa (SMB mount točke); samo mape ispod
            # ovih smiju se registrirati/čitati. realpath da simlink ne zaobiđe scoping.
            mount_roots=[os.path.realpath(os.path.expanduser(p))
                         for p in e("RAGSPINE_MOUNT_ROOTS", "").split(",") if p.strip()],
            digest_hour=int(e("RAGSPINE_DIGEST_HOUR", "7")))

_cfg: Config | None = None
def get_config() -> Config:
    global _cfg
    if _cfg is None: _cfg = Config.from_env()
    return _cfg
def set_config(cfg: Config | None):
    global _cfg; _cfg = cfg
