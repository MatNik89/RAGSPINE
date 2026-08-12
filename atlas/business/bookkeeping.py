# Posting (knjizenje): account suggestion + tax deductibility, with learning from corrections.
#
# Priority: (1) learned from konto_corrections, (2) regex rule from
# categorization.RULES, (3) search of the chart of accounts by keywords,
# (4) generic fallback.
import re

from atlas.business import feedback_learn, categorization

_LEAD_PATTERNS = [
    r"^kako\s+(da\s+)?(pro)?knji[žz]im\s*",
    r"^kako\s+se\s+(pro)?knji[žz]i\s*",
    r"^na\s+koj\w*\s+konto\s*(knji[žz]im|ide|treba)?\s*",
    r"^gdje\s+(se\s+)?knji[žz]i\s*",
    r"^proknji[žz]i(te)?\s*",
]
_TRAILING = " ?.!:-"


def _extract_description(query: str) -> str:
    q = (query or "").strip()
    for pattern in _LEAD_PATTERNS:
        stripped = re.sub(pattern, "", q, flags=re.IGNORECASE)
        if stripped != q:
            q = stripped
            break
    return q.strip(_TRAILING)


def _naziv_for_konto(spine, konto: str) -> str:
    row = spine.read().execute("SELECT naziv FROM kontni_plan WHERE konto=?", (konto,)).fetchone()
    if row is not None:
        return row["naziv"]
    for rule in categorization.RULES:
        if rule["konto"] == konto:
            return rule["naziv"]
    return konto


def _porezno_note_for_konto(konto: str) -> tuple[float, str]:
    for rule in categorization.RULES:
        if rule["konto"] == konto:
            return rule["porezno_priznato"], rule["note"]
    return 1.0, "Naučeno iz prethodnih ispravki — provjeri poreznu priznatost."


def _search_kontni_plan(spine, description: str) -> dict | None:
    words = feedback_learn._significant_words(feedback_learn._norm(description))
    if not words:
        return None
    stems = {feedback_learn._stem(w) for w in words}
    rows = spine.read().execute("SELECT konto, naziv FROM kontni_plan").fetchall()
    for row in rows:
        naziv_stems = {feedback_learn._stem(w) for w in
                        feedback_learn._significant_words(feedback_learn._norm(row["naziv"]), limit=None)}
        if naziv_stems & stems:
            return {"konto": row["konto"], "naziv": row["naziv"],
                    "porezno_priznato": 1.0,
                    "note": "Pronađeno pretragom kontnog plana — provjeri."}
    return None


def suggest(spine, description: str) -> dict:
    learned = feedback_learn.suggest_from_feedback(spine, description)
    if learned is not None:
        naziv = _naziv_for_konto(spine, learned["konto"])
        porezno, note = _porezno_note_for_konto(learned["konto"])
        return {"konto": learned["konto"], "naziv": naziv, "porezno_priznato": porezno,
                "note": note, "confidence": learned["confidence"], "source": "naučeno"}

    cat = categorization.categorize(description)
    if cat["matched"]:
        return {"konto": cat["konto"], "naziv": cat["naziv"],
                "porezno_priznato": cat["porezno_priznato"], "note": cat["note"],
                "confidence": 0.8, "source": "pravilo"}

    kp = _search_kontni_plan(spine, description)
    if kp is not None:
        return {**kp, "confidence": 0.5, "source": "kontni-plan"}

    return {"konto": cat["konto"], "naziv": cat["naziv"],
            "porezno_priznato": cat["porezno_priznato"], "note": cat["note"],
            "confidence": 0.2, "source": "nesigurno"}


def handle(spine, cfg, query: str, llm) -> str:
    description = _extract_description(query)
    result = suggest(spine, description)
    pct = int(round(result["porezno_priznato"] * 100))
    return (f"Prijedlog konta: {result['konto']} {result['naziv']} ({pct}% porezno priznato). "
            f"Izvor: {result['source']}. Ako je krivo, ispravi pa ću zapamtiti.")


from atlas.rag import pipeline  # noqa: E402  (lazy: avoid any import-order coupling)
pipeline.LANE_HANDLERS["knjizenje"] = handle
