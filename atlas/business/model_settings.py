# "Mozak vs gorivo": odabir LLM providera živi u bazi (config_overrides, modul
# 'model'), ne u kodu ni u okolišu. ATLAS ostaje isti; model je zamjenjiv.
# api_key se sprema, ali se nikad ne vraća u čistom obliku (samo has_api_key).

import dataclasses
import socket
import urllib.parse

from atlas.core.net import _is_blocked_addr

# B11: statični katalog providera (ARHITEKTURA.md §5.1) — namjerno nema ekran za
# uređivanje (audit "Što NE dirati" #3) niti popis modela po provideru (#4); tipični
# modeli idu kao <datalist> prijedlog u UI, ne ovdje. "custom" = ručni unos base_url-a.
# path = put iza base_url za OpenAI-kompat pozive (core/llm.py); "" znači zadano
# "/v1/chat/completions" (core/llm.py). Gemini ima nestandardni put (B10).
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
    """Za anthropic/openai base_url: https + javni host (ne interni/loopback).
    Sprječava da autenticirani korisnik preusmjeri spremljeni API ključ na
    napadačev/interni host preko /model/test (SSRF + eksfiltracija ključa)."""
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
    """Za UI — bez sirovog ključa."""
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
    # remote provideri (svi osim Ollame): base_url mora biti javni https (ako je zadan).
    # B11 proširio popis providera preko anthropic/openai — isti SSRF/eksfiltracijski
    # rizik vrijedi za svakog od njih (i za "custom"), pa se uvjet drži na "nije ollama".
    if provider != "ollama" and base_url:
        _validate_remote_url(base_url)

    prev = _raw(spine)
    # Ako se ENDPOINT (provider ili base_url) promijeni, a novi ključ nije upisan,
    # obriši stari ključ — inače bi se stari ključ poslao na novi host.
    endpoint_changed = (prev["provider"] != provider) or (prev["base_url"] != base_url)
    if endpoint_changed and not api_key:
        spine.set_override("model", "api_key", "")

    spine.set_override("model", "provider", provider)
    spine.set_override("model", "model", model or "")
    spine.set_override("model", "base_url", base_url)
    spine.set_override("model", "embed_model", embed_model or "")
    spine.set_override("model", "ollama_url", ollama_url or "")
    # prazan api_key = zadrži postojeći (osim gornjeg endpoint-changed slučaja).
    # Šifriraj at-rest kad je cfg dostupan (ukradeni backup ne otkriva ključ; Codex);
    # bez cfg (npr. wizard/testovi) ostaje plaintext, apply() svejedno dešifrira.
    if api_key:
        if cfg is not None:
            from atlas.business import secretbox
            api_key = secretbox.encrypt(api_key, cfg)
        spine.set_override("model", "api_key", api_key)
    spine.audit(user, "model_settings_save", f"provider:{provider}")
    return get(spine)


def apply(spine, cfg):
    """Vrati cfg s DB-odabirom modela (primarni provider). Bez odabira → cfg."""
    s = _raw(spine)
    if s.get("api_key"):  # dešifriraj at-rest ključ (secretbox fallback = stari plaintext)
        from atlas.business import secretbox
        s = {**s, "api_key": secretbox.decrypt(s["api_key"], cfg)}
    return _apply_profile(cfg, s)


def _apply_profile(cfg, s: dict):
    """Nadjačaj cfg jednim provider-profilom (dict: provider/model/base_url/
    api_key/embed_model/ollama_url). Immutable copy (dataclasses.replace).
    Dijele apply() i chain() — jedan izvor logike po provideru."""
    prov = s.get("provider")
    if not prov:
        return cfg
    model = s.get("model") or cfg.llm_model
    base = s.get("base_url") or ""
    embed = s.get("embed_model") or cfg.embed_model
    if prov == "ollama":
        # llm_path="" — reset od eventualnog prijašnjeg openai-kompat odabira (npr.
        # Gemini); inače cfg nosi tuđi put dok se aplikacija ne restarta (review nalaz).
        return dataclasses.replace(cfg, llm_provider="ollama", llm_base_url="", llm_api_key="",
                                   llm_model=model, llm_path="",
                                   ollama_url=(s.get("ollama_url") or base or cfg.ollama_url),
                                   embed_model=embed)
    if prov == "anthropic":
        b = base or cfg.anthropic_base_url
        return dataclasses.replace(cfg, llm_provider="anthropic", llm_base_url=b,
                                   llm_api_key=s.get("api_key"), llm_model=model, llm_path="",
                                   anthropic_base_url=b, embed_model=embed)
    # openai-compat (svi ostali katalog ključevi, uklj. "custom"): put iza base_url
    # dolazi iz kataloga (B10) — zadano "/v1/chat/completions" ako ključ nije poznat.
    entry = _CATALOG_BY_KEY.get(prov, {})
    return dataclasses.replace(cfg, llm_provider="openai",
                               llm_base_url=(base or entry.get("base_url") or "https://api.openai.com"),
                               llm_api_key=s.get("api_key"), llm_model=model, embed_model=embed,
                               llm_path=entry.get("path") or "/v1/chat/completions")


_FB_KEYS = ("provider", "model", "base_url", "api_key", "ollama_url")


def get_fallbacks(spine) -> list[dict]:
    """Za UI — bez sirovog ključa (has_api_key umjesto vrijednosti)."""
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
    """Profili s DEŠIFRIRANIM ključem (za gradnju lanca). Nikad se ne izlaže rutom."""
    from atlas.business import secretbox
    out = []
    for p in _fallbacks_raw(spine):
        d = {k: p.get(k, "") for k in _FB_KEYS}
        d["api_key"] = secretbox.decrypt(d.get("api_key") or "", cfg)
        out.append(d)
    return out


def set_fallbacks(spine, profiles: list[dict], cfg, user: str = "?") -> None:
    """Spremi uređeni popis fallback-providera (šifriraj ključeve). Prazna lista =
    isključi fallback. base_url ide kroz istu SSRF-provjeru kao primarni."""
    import json

    from atlas.business import secretbox
    if len(profiles or []) > 5:
        raise ValueError("najviše 5 zamjenskih providera")  # anti-amplifikacija (Codex)
    # prijašnji (šifrirani) ključevi po (provider, base_url) — prazan novi ključ
    # znači ZADRŽI stari (GET maskira ključ pa read-edit-write ne smije obrisati; Codex)
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
    """Uređeni lanac primijenjenih cfg-ova: [primarni] + fallbacks. Prazan primarni
    provider -> samo cfg (kao dosad). FallbackLLM ih redom pokušava."""
    primary = apply(spine, cfg)
    return [primary] + [_apply_profile(cfg, fb) for fb in fallbacks(spine, cfg)]


def build_llm(spine, cfg, transport=None):
    """Jedinstveni ulaz za gradnju LLM-a: lanac providera s fallbackom. Ako nema
    fallbacka, ponaša se kao jedan LLMClient (lanac duljine 1).

    Rezidualno (prihvaćeno, Codex): (a) ollama_url je lokalni-po-dizajnu pa se ne
    SSRF-provjerava (kao primarni); (b) llm.py transport re-resolva DNS (rebind) —
    provider-URL je admin-konfiguriran na poznat endpoint, ne user-content; (c) ako
    primarni podržava alate a zamjenski ne, zamjenski samo tekstualno odgovori
    (agent ne dobije tool_call = ne predloži write; degradacija, ne rupa)."""
    from atlas.core.llm import FallbackLLM
    return FallbackLLM(chain(spine, cfg), transport=transport)


def test_connection(spine, cfg) -> dict:
    """Pokušaj kratki upit s trenutnim odabirom; vrati {ok, error?}."""
    from atlas.core.llm import LLMClient, LLMError, LLMUnavailable
    try:
        res = LLMClient(apply(spine, cfg)).complete(
            [{"role": "user", "content": "ping"}], max_tokens=5)
        return {"ok": True, "model": res.model}
    except (LLMError, LLMUnavailable) as e:
        return {"ok": False, "error": str(e)[:300]}
    except Exception as e:  # mreža/timeout/itd. — ne ruši UI
        return {"ok": False, "error": str(e)[:300]}
