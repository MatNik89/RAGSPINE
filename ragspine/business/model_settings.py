# "Mozak vs gorivo": odabir LLM providera živi u bazi (config_overrides, modul
# 'model'), ne u kodu ni u okolišu. RAGSPINE ostaje isti; model je zamjenjiv.
# api_key se sprema, ali se nikad ne vraća u čistom obliku (samo has_api_key).

import dataclasses

PROVIDERS = ("anthropic", "openai", "ollama")
_KEYS = ("provider", "model", "base_url", "api_key", "embed_model", "ollama_url")


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
         embed_model: str = "", ollama_url: str = "", user: str = "?") -> dict:
    if provider not in PROVIDERS:
        raise ValueError(f"nepoznat provider: {provider!r}")
    spine.set_override("model", "provider", provider)
    spine.set_override("model", "model", model or "")
    spine.set_override("model", "base_url", base_url or "")
    spine.set_override("model", "embed_model", embed_model or "")
    spine.set_override("model", "ollama_url", ollama_url or "")
    # prazan api_key = zadrži postojeći (ne briši nehotice)
    if api_key:
        spine.set_override("model", "api_key", api_key)
    spine.audit(user, "model_settings_save", f"provider:{provider}")
    return get(spine)


def apply(spine, cfg):
    """Vrati cfg s DB-odabirom modela nadjačanim preko env-a. Bez odabira → cfg."""
    s = _raw(spine)
    prov = s["provider"]
    if not prov:
        return cfg
    model = s["model"] or cfg.llm_model
    base = s["base_url"]
    embed = s["embed_model"] or cfg.embed_model
    if prov == "ollama":
        return dataclasses.replace(cfg, llm_base_url="", llm_api_key="", llm_model=model,
                                   ollama_url=base or cfg.ollama_url, embed_model=embed)
    if prov == "anthropic":
        b = base or cfg.anthropic_base_url
        return dataclasses.replace(cfg, llm_base_url=b, llm_api_key=s["api_key"],
                                   llm_model=model, anthropic_base_url=b, embed_model=embed)
    # openai-compat
    return dataclasses.replace(cfg, llm_base_url=base or "https://api.openai.com/v1",
                               llm_api_key=s["api_key"], llm_model=model, embed_model=embed)


def test_connection(spine, cfg) -> dict:
    """Pokušaj kratki upit s trenutnim odabirom; vrati {ok, error?}."""
    from ragspine.core.llm import LLMClient, LLMError, LLMUnavailable
    try:
        res = LLMClient(apply(spine, cfg)).complete(
            [{"role": "user", "content": "ping"}], max_tokens=5)
        return {"ok": True, "model": res.model}
    except (LLMError, LLMUnavailable) as e:
        return {"ok": False, "error": str(e)[:300]}
    except Exception as e:  # mreža/timeout/itd. — ne ruši UI
        return {"ok": False, "error": str(e)[:300]}
