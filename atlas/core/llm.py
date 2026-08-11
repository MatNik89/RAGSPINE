"""LLM provider dispatcher: OpenAI-compat + Anthropic + Ollama + OAuth fallback."""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
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
    tool_calls: list[dict] = field(default_factory=list)


def _tools_anthropic(tools: list[dict]) -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]}
            for t in tools]


def _tools_openai(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["schema"]}}
        for t in tools]


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


class FallbackLLM:
    """Lanac providera: pokušava redom, na LLMUnavailable/LLMError prelazi na
    sljedeći; digne zadnju grešku ako svi padnu. Sučelje isto kao LLMClient
    (supports_tools/complete) pa ga pozivatelji ne razlikuju. `last_used` pamti
    indeks providera koji je uspio (za dijagnostiku)."""
    def __init__(self, cfgs: list, transport=None):
        self._clients = [LLMClient(c, transport) for c in (cfgs or [])]
        self.last_used = None

    def supports_tools(self) -> bool:
        return self._clients[0].supports_tools() if self._clients else True

    def complete(self, messages, system=None, model=None, max_tokens=1024,
                 temperature=0.2, tools=None) -> "LLMResult":
        import logging
        last_err = None
        for i, client in enumerate(self._clients):
            try:
                res = client.complete(messages, system=system, model=model,
                                      max_tokens=max_tokens, temperature=temperature, tools=tools)
                self.last_used = i
                if i > 0:  # vidljivost: upit je otišao na ZAMJENSKOG providera (data-residency)
                    logging.getLogger(__name__).warning(
                        "LLM fallback: primarni pao, poslužio provider #%d", i)
                return res
            # uklj. OSError -> socket-timeout/URLError koji nisu omotani kao LLMError
            # ne smiju prekinuti lanac (Codex); ostalo (bug) neka se digne
            except (LLMUnavailable, LLMError, OSError) as e:
                last_err = e  # probaj sljedeći provider u lancu
        raise last_err or LLMUnavailable("no LLM provider in chain")


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

    def supports_tools(self) -> bool:
        # ponytail: ollama ovisi o modelu — agent hvata grešku/degradira, pa
        # umjesto probe-poziva ovdje samo javljamo "pokušaj" (True). Upgrade
        # path: cache-irati stvarni ishod prvog pokušaja po modelu.
        return True

    def complete(self, messages: list[dict], system: str | None = None,
                 model=None, max_tokens: int = 1024, temperature: float = 0.2,
                 tools: list[dict] | None = None) -> LLMResult:
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
            if tools:
                body["tools"] = _tools_anthropic(tools)
            resp = self.transport(url, headers, body)
            if not tools:
                try:
                    blocks = resp["content"]
                    block = next((b for b in blocks if b.get("type") == "text"), blocks[0])
                    text = block["text"]
                except (KeyError, IndexError, TypeError):
                    raise LLMError(f"malformed provider response: {resp}") from None
                return LLMResult(text=text, model=resp.get("model", model or ""), usage=resp.get("usage", {}))
            try:
                blocks = resp["content"]
                text_block = next((b for b in blocks if b.get("type") == "text"), None)
                tool_calls = [{"name": b["name"], "args": b.get("input", {})}
                              for b in blocks if b.get("type") == "tool_use"]
                text = text_block["text"] if text_block is not None else ""
                if text_block is None and not tool_calls:
                    text = blocks[0]["text"]  # ni text ni tool_use: čuvaj staro (moguće) rušenje
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"malformed provider response: {resp}") from None
            return LLMResult(text=text, model=resp.get("model", model or ""),
                              usage=resp.get("usage", {}), tool_calls=tool_calls)

        # B10: put iza base_url dolazi iz kataloga (business/model_settings.py PROVIDER_CATALOG),
        # koji apply() upiše u cfg.llm_path po odabranom provideru; prazno/zadano = staro ponašanje.
        path = getattr(cfg, "llm_path", "") or "/v1/chat/completions"
        url = f"{base.rstrip('/')}{path}"
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": msgs}
        if not tools:
            resp = self.transport(url, headers, body)
            try:
                text = resp["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"malformed provider response: {resp}") from None
            return LLMResult(text=text, model=resp.get("model", model or ""), usage=resp.get("usage", {}))

        body["tools"] = _tools_openai(tools)
        body["tool_choice"] = "auto"
        resp = self.transport(url, headers, body)
        try:
            message = resp["choices"][0]["message"]
            text = message.get("content") or ""
            tool_calls = []
            for tc in message.get("tool_calls") or []:
                fn = tc["function"]
                args_raw = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                except ValueError:
                    args = {}
                tool_calls.append({"name": fn["name"], "args": args})
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"malformed provider response: {resp}") from None
        return LLMResult(text=text, model=resp.get("model", model or ""),
                          usage=resp.get("usage", {}), tool_calls=tool_calls)
