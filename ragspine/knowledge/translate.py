"""LLM-backed translation into 16 languages."""

LANGS = {
    "hr": "Croatian", "en": "English", "de": "German", "it": "Italian",
    "sl": "Slovenian", "fr": "French", "es": "Spanish", "sr": "Serbian",
    "bs": "Bosnian", "mk": "Macedonian", "hu": "Hungarian", "cs": "Czech",
    "sk": "Slovak", "pl": "Polish", "ru": "Russian", "uk": "Ukrainian",
}


def translate(llm, text: str, target: str) -> str:
    if target not in LANGS:
        raise ValueError(f"nepoznat jezik: {target!r}")
    if llm is None:
        raise ValueError("LLM nedostupan")
    system = (
        f"Translate the user's text to {LANGS[target]}. "
        "Return ONLY the translation, no explanations."
    )
    result = llm.complete([{"role": "user", "content": text}], system=system)
    return result.text
