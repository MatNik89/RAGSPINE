"""LLM provider dispatcher: OpenAI-compat + Anthropic + Ollama + OAuth fallback."""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class LLMUnavailable(Exception):
    pass


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    usage: dict


def detect_provider(base_url: str) -> str:
    if "anthropic" in base_url:
        return "anthropic"
    if ":11434" in base_url or base_url.rstrip("/").endswith("/api"):
        return "ollama"
    return "openai"


def load_oauth_token() -> tuple[str, str] | None:
    try:
        data = json.loads(Path("~/.claude/.credentials.json").expanduser().read_text(encoding="utf-8"))
        token = data["claudeAiOauth"]["accessToken"]
        return ("anthropic-oauth", token)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        data = json.loads(Path("~/.codex/auth.json").expanduser().read_text(encoding="utf-8"))
        token = data["tokens"]["access_token"]
        return ("openai-oauth", token)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _ollama_alive(cfg) -> bool:
    try:
        with urllib.request.urlopen(f"{cfg.ollama_url}/api/tags", timeout=2):
            return True
    except Exception:
        return False


def _default_transport(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise LLMError(e.read().decode(errors="replace")) from e
    except urllib.error.URLError as e:
        raise LLMError(str(e)) from e


class LLMClient:
    def __init__(self, cfg, transport=None):
        self.cfg = cfg
        self.transport = transport or _default_transport

    def _resolve(self):
        """Returns (provider, base_url, key, is_oauth)."""
        cfg = self.cfg
        # Eksplicitni odabir (Postavke → Model) ima prednost pred URL-heuristikom:
        # custom proxy bez "anthropic" u imenu inače bi krivo išao OpenAI formatom.
        prov = getattr(cfg, "llm_provider", "")
        if prov == "ollama":
            return "ollama", cfg.ollama_url, None, False
        if prov in ("anthropic", "openai") and cfg.llm_api_key:
            base = cfg.llm_base_url or (
                cfg.anthropic_base_url if prov == "anthropic" else "https://api.openai.com")
            return prov, base, cfg.llm_api_key, False
        if cfg.llm_base_url and cfg.llm_api_key:
            return detect_provider(cfg.llm_base_url), cfg.llm_base_url, cfg.llm_api_key, False
        if _ollama_alive(cfg):
            return "ollama", cfg.ollama_url, None, False
        oauth = load_oauth_token()
        if oauth:
            provider, token = oauth
            if provider == "anthropic-oauth":
                return "anthropic", cfg.anthropic_base_url, token, True
            return "openai", cfg.llm_base_url or "https://api.openai.com", token, True
        raise LLMUnavailable("no LLM provider configured: no base_url/key, Ollama unreachable, no OAuth token")

    def complete(self, messages: list[dict], system: str | None = None,
                 model=None, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResult:
        cfg = self.cfg
        provider, base, key, is_oauth = self._resolve()
        model = model or cfg.llm_model

        if provider == "anthropic":
            url = f"{base}/v1/messages"
            headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
            if is_oauth:
                headers["authorization"] = f"Bearer {key}"
            else:
                headers["x-api-key"] = key
            body = {"model": model, "max_tokens": max_tokens, "messages": messages}
            if system is not None:
                body["system"] = system
            resp = self.transport(url, headers, body)
            try:
                blocks = resp["content"]
                block = next((b for b in blocks if b.get("type") == "text"), blocks[0])
                text = block["text"]
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"malformed provider response: {resp}") from None
            return LLMResult(text=text, model=resp.get("model", model or ""), usage=resp.get("usage", {}))

        url = f"{base}/v1/chat/completions"
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": msgs}
        resp = self.transport(url, headers, body)
        try:
            text = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"malformed provider response: {resp}") from None
        return LLMResult(text=text, model=resp.get("model", model or ""), usage=resp.get("usage", {}))
