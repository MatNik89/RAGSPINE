"""LLM provider dispatcher: OpenAI-compat + Anthropic + Ollama + OAuth fallback."""
import email.utils
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


class LLMUnavailable(Exception):
    pass


class LLMError(Exception):
    status: int | None = None          # HTTP status (429/401/...) if known
    retry_after_ms: int | None = None  # provider-stated window (Retry-After)


_MAX_RETRY_AFTER_MS = 2 * 60 * 60 * 1000  # 2h upper bound (anti-absurd)


def _parse_retry_after(val: str | None) -> int | None:
    """Retry-After: seconds or an HTTP-date -> ms (clamp 1s..2h)."""
    if not val:
        return None
    val = val.strip()
    try:
        return max(1000, min(int(float(val)) * 1000, _MAX_RETRY_AFTER_MS))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(val)
        import datetime as _d
        delta = (dt - _d.datetime.now(dt.tzinfo)).total_seconds()
        if delta > 0:
            return max(1000, min(int(delta * 1000), _MAX_RETRY_AFTER_MS))
    except (TypeError, ValueError):
        pass
    return None


class ProviderHealthTracker:
    """Parks a failed provider on a cooldown so the fallback does not try it on
    every request (the chain otherwise blindly retried a dead one). Consecutive
    failures >= threshold -> park for cooldown_ms; a provider-stated Retry-After
    parks IMMEDIATELY (never shortens an active cooldown). Lazy expiry (no
    thread). Process-local; `clock` is injectable (ms) for tests. MateClaw
    ProviderHealthTracker pattern."""
    def __init__(self, threshold: int = 3, cooldown_ms: int = 300_000, clock=None):
        import threading
        self.threshold = max(1, threshold)
        self.cooldown_ms = max(1000, cooldown_ms)
        self._clock = clock or (lambda: time.monotonic() * 1000)
        self._fails: dict[str, int] = {}
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()  # FastAPI routes run in threads -> lock the maps (Codex)

    def is_in_cooldown(self, key: str) -> bool:
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return False
            if self._clock() >= until:  # expired -> clear, the next call tries
                self._until.pop(key, None)
                self._fails.pop(key, None)
                return False
            return True

    def record_failure(self, key: str, retry_after_ms: int | None = None) -> None:
        with self._lock:
            if retry_after_ms:  # provider said when -> park now, never shorten
                self._until[key] = max(self._until.get(key, 0),
                                       self._clock() + min(retry_after_ms, _MAX_RETRY_AFTER_MS))
                return
            n = self._fails.get(key, 0) + 1
            self._fails[key] = n
            if n >= self.threshold:  # max() -> do not shorten a longer active cooldown (Codex)
                self._until[key] = max(self._until.get(key, 0), self._clock() + self.cooldown_ms)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._until.pop(key, None)


_health = ProviderHealthTracker()  # shared singleton (park survives turns)

# Default models for zero-config OAuth (token auto-detected, operator picked no model).
# Anthropic default is verified working via the Claude subscription OAuth token; the
# operator can always override in Settings -> Model.
_DEFAULT_OAUTH_MODEL = {"anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-5"}


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
        err = LLMError(e.read().decode(errors="replace"))
        err.status = e.code
        try:
            err.retry_after_ms = _parse_retry_after(e.headers.get("Retry-After"))
        except Exception:
            err.retry_after_ms = None
        raise err from e
    except urllib.error.URLError as e:
        raise LLMError(str(e)) from e


def _provider_key(cfg) -> str:
    """Stable provider identity for the health-tracker: provider+endpoint+path+model
    + a SHORT hash of the key (two profiles with different key/account do not share
    a cooldown; we never put the raw key in the map; Codex)."""
    if cfg is None:
        return "none"
    prov = getattr(cfg, "llm_provider", "") or ""
    base = getattr(cfg, "llm_base_url", "") or getattr(cfg, "ollama_url", "") or ""
    path = getattr(cfg, "llm_path", "") or ""
    model = getattr(cfg, "llm_model", "") or ""
    key = getattr(cfg, "llm_api_key", "") or ""
    kh = __import__("hashlib").sha256(key.encode()).hexdigest()[:8] if key else ""
    return f"{prov}|{base}{path}|{model}|{kh}"


def _counts_health(e) -> bool:
    """Whether an error counts toward the cooldown. A request-specific 4xx (bad
    request/too long/unknown model) is NOT a provider failure -> do not park a
    healthy provider (Codex). Timeout/network (OSError, LLMUnavailable) and
    429/5xx/auth do count."""
    st = getattr(e, "status", None)
    if isinstance(e, LLMError) and st in (400, 404, 413, 422):
        return False
    return True


class FallbackLLM:
    """Provider chain: tries in order, on LLMUnavailable/LLMError moves to the
    next; raises the last error if all fail. Same interface as LLMClient
    (supports_tools/complete) so callers cannot tell them apart. `last_used`
    remembers the index of the provider that succeeded (for diagnostics)."""
    def __init__(self, cfgs: list, transport=None, health=None):
        self._clients = [LLMClient(c, transport) for c in (cfgs or [])]
        self._keys = [_provider_key(c) for c in (cfgs or [])]
        self._health = health or _health
        self.last_used = None

    def supports_tools(self) -> bool:
        return self._clients[0].supports_tools() if self._clients else True

    def _try(self, i, kwargs):
        key = self._keys[i]
        res = self._clients[i].complete(**kwargs)
        self._health.record_success(key)
        self.last_used = i
        if i > 0:  # visibility: the request went to a FALLBACK provider (data-residency)
            import logging
            logging.getLogger(__name__).warning(
                "LLM fallback: primarni pao/na cooldownu, poslužio provider #%d", i)
        return res

    def complete(self, messages, system=None, model=None, max_tokens=1024,
                 temperature=0.2, tools=None) -> "LLMResult":
        kwargs = dict(messages=messages, system=system, model=model,
                      max_tokens=max_tokens, temperature=temperature, tools=tools)
        last_err = None
        tried = False
        for i in range(len(self._clients)):
            if self._health.is_in_cooldown(self._keys[i]):
                continue  # parked provider -> skip (do not waste effort)
            tried = True
            try:
                return self._try(i, kwargs)
            except (LLMUnavailable, LLMError, OSError) as e:
                (_counts_health(e) and self._health.record_failure(self._keys[i], getattr(e, "retry_after_ms", None)))
                last_err = e
        # all parked -> last chance: try them ignoring the cooldown (do not hang without an LLM)
        if not tried:
            for i in range(len(self._clients)):
                try:
                    return self._try(i, kwargs)
                except (LLMUnavailable, LLMError, OSError) as e:
                    (_counts_health(e) and self._health.record_failure(self._keys[i], getattr(e, "retry_after_ms", None)))
                    last_err = e
        raise last_err or LLMUnavailable("no LLM provider in chain")


class LLMClient:
    def __init__(self, cfg, transport=None):
        self.cfg = cfg
        self.transport = transport or _default_transport

    def _resolve(self):
        """Returns (provider, base_url, key, is_oauth)."""
        cfg = self.cfg
        # An explicit choice (Settings -> Model) takes precedence over the URL
        # heuristic: a custom proxy without "anthropic" in its name would
        # otherwise wrongly go via the OpenAI format.
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
        # ponytail: ollama depends on the model — the agent catches the error/
        # degrades, so instead of a probe call here we just report "try" (True).
        # Upgrade path: cache the actual outcome of the first attempt per model.
        return True

    def complete(self, messages: list[dict], system: str | None = None,
                 model=None, max_tokens: int = 1024, temperature: float = 0.2,
                 tools: list[dict] | None = None) -> LLMResult:
        cfg = self.cfg
        provider, base, key, is_oauth = self._resolve()
        model = model or cfg.llm_model
        # Zero-config OAuth (auto-detected Claude/OpenAI token, no model chosen): default a
        # sensible model instead of failing with a cryptic "model too short" provider error.
        # Only for OAuth — an explicitly-configured provider with no model is an operator error.
        if not model and is_oauth:
            model = _DEFAULT_OAUTH_MODEL.get(provider, "")

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
                    text = blocks[0]["text"]  # neither text nor tool_use: keep the old (possible) crash
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"malformed provider response: {resp}") from None
            return LLMResult(text=text, model=resp.get("model", model or ""),
                              usage=resp.get("usage", {}), tool_calls=tool_calls)

        # B10: the path after base_url comes from the catalog (business/model_settings.py PROVIDER_CATALOG),
        # which apply() writes into cfg.llm_path per selected provider; empty/default = old behavior.
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
