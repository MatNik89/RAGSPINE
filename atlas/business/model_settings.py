# "Brain vs fuel": the LLM provider choice lives in the database (config_overrides,
# module 'model'), not in code nor in the environment. ATLAS stays the same; the
# model is replaceable. api_key is stored but never returned in cleartext (only has_api_key).

import dataclasses
import socket
import urllib.parse

from atlas.core.net import _is_blocked_addr

# B11: static provider catalog (ARHITEKTURA.md §5.1) — deliberately has no editing
# screen (audit "What NOT to touch" #3) nor a per-provider model list (#4); typical
# models go as a <datalist> suggestion in the UI, not here. "custom" = manual base_url entry.
# path = the path after base_url for OpenAI-compat calls (core/llm.py); "" means the
# default "/v1/chat/completions" (core/llm.py). Gemini has a non-standard path (B10).
PROVIDER_CATALOG = [
    {"key": "anthropic", "naziv": "Anthropic (Claude)", "base_url": "https://api.anthropic.com",
     "path": "/v1/messages", "needs_key": True, "hint": "Zahtijeva API ključ s console.anthropic.com."},
    {"key": "openai", "naziv": "OpenAI-compat (ChatGPT i sl.)", "base_url": "https://api.openai.com",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "OpenAI ili bilo koji OpenAI-kompatibilan servis."},
    {"key": "ollama", "naziv": "Lokalni (Ollama)", "base_url": "http://127.0.0.1:11434",
     "path": "/v1/chat/completions", "needs_key": False, "hint": "Lokalni server; ključ nije potreban."},
    {"key": "deepseek", "naziv": "DeepSeek", "base_url": "https://api.deepseek.com",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "OpenAI-kompatibilan API."},
    {"key": "moonshot", "naziv": "Moonshot / Kimi", "base_url": "https://api.moonshot.ai",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "OpenAI-kompatibilan API."},
    {"key": "groq", "naziv": "Groq", "base_url": "https://api.groq.com/openai",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "Brza inferencija; OpenAI-kompatibilan API."},
    {"key": "mistral", "naziv": "Mistral", "base_url": "https://api.mistral.ai",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "OpenAI-kompatibilan API."},
    {"key": "openrouter", "naziv": "OpenRouter", "base_url": "https://openrouter.ai/api",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "Agregator više providera iza jednog ključa."},
    {"key": "xai", "naziv": "xAI (Grok)", "base_url": "https://api.x.ai",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "OpenAI-kompatibilan API."},
    {"key": "together", "naziv": "Together AI", "base_url": "https://api.together.xyz",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "OpenAI-kompatibilan API."},
    {"key": "gemini", "naziv": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com",
     "path": "/v1beta/openai/chat/completions", "needs_key": True,
     "hint": "OpenAI-kompatibilan endpoint na nestandardnom putu."},
    {"key": "custom", "naziv": "Ručni unos…", "base_url": "",
     "path": "/v1/chat/completions", "needs_key": True,
     "hint": "Ručno upiši base URL bilo kojeg OpenAI-kompatibilnog servera."},
]
_CATALOG_BY_KEY = {p["key"]: p for p in PROVIDER_CATALOG}
PROVIDERS = tuple(p["key"] for p in PROVIDER_CATALOG)
_KEYS = ("provider", "model", "base_url", "api_key", "embed_model", "ollama_url")


def _validate_remote_url(url: str) -> None:
    """For anthropic/openai base_url: https + public host (not internal/loopback).
    Prevents an authenticated user from redirecting the stored API key to an
    attacker/internal host via /model/test (SSRF + key exfiltration)."""
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise ValueError("base_url mora biti https za Claude/OpenAI")
    host = p.hostname
    if not host:
        raise ValueError("base_url nema host")
    try:
        addrs = socket.getaddrinfo(host, p.port or 443)
    except OSError as e:
        raise ValueError(f"base_url host se ne razrješava: {e}") from e
    if any(_is_blocked_addr(sa[4][0]) for sa in addrs):
        raise ValueError("base_url pokazuje na interni/privatni host — nije dozvoljeno")


def _raw(spine) -> dict:
    return {k: (spine.get_override("model", k) or "") for k in _KEYS}


def get(spine) -> dict:
    """For the UI — without the raw key."""
    r = _raw(spine)
    return {
        "provider": r["provider"], "model": r["model"], "base_url": r["base_url"],
        "embed_model": r["embed_model"], "ollama_url": r["ollama_url"],
        "has_api_key": bool(r["api_key"]),
    }


def save(spine, provider: str, model: str = "", base_url: str = "", api_key: str = "",
         embed_model: str = "", ollama_url: str = "", user: str = "?", cfg=None) -> dict:
    if provider not in PROVIDERS:
        raise ValueError(f"nepoznat provider: {provider!r}")
    base_url = (base_url or "").strip()
    # remote providers (all except Ollama): base_url must be public https (if given).
    # B11 expanded the provider list beyond anthropic/openai — the same SSRF/exfiltration
    # risk applies to each of them (and to "custom"), so the condition stays as "not ollama".
    if provider != "ollama" and base_url:
        _validate_remote_url(base_url)

    prev = _raw(spine)
    # If the ENDPOINT (provider or base_url) changes and a new key was not entered,
    # delete the old key — otherwise the old key would be sent to the new host.
    endpoint_changed = (prev["provider"] != provider) or (prev["base_url"] != base_url)
    if endpoint_changed and not api_key:
        spine.set_override("model", "api_key", "")

    spine.set_override("model", "provider", provider)
    spine.set_override("model", "model", model or "")
    spine.set_override("model", "base_url", base_url)
    spine.set_override("model", "embed_model", embed_model or "")
    spine.set_override("model", "ollama_url", ollama_url or "")
    # empty api_key = keep the existing one (except the endpoint-changed case above).
    # Encrypt at-rest when cfg is available (a stolen backup does not reveal the key; Codex);
    # without cfg (e.g. wizard/tests) it stays plaintext, apply() decrypts anyway.
    if api_key:
        if cfg is not None:
            from atlas.business import secretbox
            api_key = secretbox.encrypt(api_key, cfg)
        spine.set_override("model", "api_key", api_key)
    spine.audit(user, "model_settings_save", f"provider:{provider}")
    return get(spine)


def apply(spine, cfg):
    """Return cfg with the DB model choice (primary provider). No choice → cfg."""
    s = _raw(spine)
    if s.get("api_key"):  # decrypt the at-rest key (secretbox fallback = old plaintext)
        from atlas.business import secretbox
        s = {**s, "api_key": secretbox.decrypt(s["api_key"], cfg)}
    return _apply_profile(cfg, s)


def _apply_profile(cfg, s: dict):
    """Override cfg with a single provider profile (dict: provider/model/base_url/
    api_key/embed_model/ollama_url). Immutable copy (dataclasses.replace).
    Shared by apply() and chain() — one source of per-provider logic."""
    prov = s.get("provider")
    if not prov:
        return cfg
    model = s.get("model") or cfg.llm_model
    base = s.get("base_url") or ""
    embed = s.get("embed_model") or cfg.embed_model
    if prov == "ollama":
        # llm_path="" — reset from a possible previous openai-compat choice (e.g.
        # Gemini); otherwise cfg carries someone else's path until the app restarts (review finding).
        return dataclasses.replace(cfg, llm_provider="ollama", llm_base_url="", llm_api_key="",
                                   llm_model=model, llm_path="",
                                   ollama_url=(s.get("ollama_url") or base or cfg.ollama_url),
                                   embed_model=embed)
    if prov == "anthropic":
        b = base or cfg.anthropic_base_url
        return dataclasses.replace(cfg, llm_provider="anthropic", llm_base_url=b,
                                   llm_api_key=s.get("api_key"), llm_model=model, llm_path="",
                                   anthropic_base_url=b, embed_model=embed)
    # openai-compat (all other catalog keys, incl. "custom"): the path after base_url
    # comes from the catalog (B10) — default "/v1/chat/completions" if the key is unknown.
    entry = _CATALOG_BY_KEY.get(prov, {})
    return dataclasses.replace(cfg, llm_provider="openai",
                               llm_base_url=(base or entry.get("base_url") or "https://api.openai.com"),
                               llm_api_key=s.get("api_key"), llm_model=model, embed_model=embed,
                               llm_path=entry.get("path") or "/v1/chat/completions")


_FB_KEYS = ("provider", "model", "base_url", "api_key", "ollama_url")


def get_fallbacks(spine) -> list[dict]:
    """For the UI — without the raw key (has_api_key instead of the value)."""
    out = []
    for p in _fallbacks_raw(spine):
        out.append({"provider": p.get("provider", ""), "model": p.get("model", ""),
                    "base_url": p.get("base_url", ""), "ollama_url": p.get("ollama_url", ""),
                    "has_api_key": bool(p.get("api_key"))})
    return out


def _fallbacks_raw(spine) -> list[dict]:
    import json
    raw = spine.get_override("model", "fallbacks", "") or ""
    try:
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def fallbacks(spine, cfg) -> list[dict]:
    """Profiles with the DECRYPTED key (for building the chain). Never exposed via a route."""
    from atlas.business import secretbox
    out = []
    for p in _fallbacks_raw(spine):
        d = {k: p.get(k, "") for k in _FB_KEYS}
        d["api_key"] = secretbox.decrypt(d.get("api_key") or "", cfg)
        out.append(d)
    return out


def set_fallbacks(spine, profiles: list[dict], cfg, user: str = "?") -> None:
    """Save the edited list of fallback providers (encrypt the keys). Empty list =
    disable fallback. base_url goes through the same SSRF check as the primary."""
    import json

    from atlas.business import secretbox
    if len(profiles or []) > 5:
        raise ValueError("najviše 5 zamjenskih providera")  # anti-amplification (Codex)
    # previous (encrypted) keys by (provider, base_url) — an empty new key
    # means KEEP the old one (GET masks the key so read-edit-write must not delete it; Codex)
    prev = {(x.get("provider"), x.get("base_url")): x.get("api_key", "")
            for x in _fallbacks_raw(spine)}
    clean = []
    for p in (profiles or []):
        prov = (p.get("provider") or "").strip()
        if prov not in PROVIDERS:
            raise ValueError(f"nepoznat provider u fallbacku: {prov!r}")
        base = (p.get("base_url") or "").strip()
        if prov != "ollama" and base:
            _validate_remote_url(base)
        key = (p.get("api_key") or "").strip()
        enc = secretbox.encrypt(key, cfg) if key else prev.get((prov, base), "")
        clean.append({
            "provider": prov, "model": (p.get("model") or "").strip()[:120],
            "base_url": base, "ollama_url": (p.get("ollama_url") or "").strip()[:200],
            "api_key": enc,
        })
    spine.set_override("model", "fallbacks", json.dumps(clean, ensure_ascii=False))
    spine.audit(user, "model_fallbacks_save", f"count:{len(clean)}")


def chain(spine, cfg) -> list:
    """Ordered chain of applied cfgs: [primary] + fallbacks. Empty primary
    provider -> just cfg (as before). FallbackLLM tries them in order."""
    primary = apply(spine, cfg)
    return [primary] + [_apply_profile(cfg, fb) for fb in fallbacks(spine, cfg)]


def build_llm(spine, cfg, transport=None):
    """Single entry point for building the LLM: a provider chain with fallback. If there
    is no fallback, it behaves like a single LLMClient (chain of length 1).

    Residual (accepted, Codex): (a) ollama_url is local-by-design so it is not
    SSRF-checked (like the primary); (b) llm.py transport re-resolves DNS (rebind) —
    the provider URL is admin-configured to a known endpoint, not user-content; (c) if
    the primary supports tools but the fallback does not, the fallback only replies with
    text (the agent gets no tool_call = proposes no write; degradation, not a hole)."""
    from atlas.core.llm import FallbackLLM
    return FallbackLLM(chain(spine, cfg), transport=transport)


def test_connection(spine, cfg) -> dict:
    """Try a short request with the current choice; return {ok, error?}."""
    from atlas.core.llm import LLMClient, LLMError, LLMUnavailable
    try:
        res = LLMClient(apply(spine, cfg)).complete(
            [{"role": "user", "content": "ping"}], max_tokens=5)
        return {"ok": True, "model": res.model}
    except (LLMError, LLMUnavailable) as e:
        return {"ok": False, "error": str(e)[:300]}
    except Exception as e:  # network/timeout/etc. — do not crash the UI
        return {"ok": False, "error": str(e)[:300]}
