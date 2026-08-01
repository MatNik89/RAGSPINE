# Učenje iz ispravki knjigovođe: bilježi original -> ispravljeni konto i
# predlaže konto iz prošlih ispravki za slične buduće opise.
import re

_DIACRITICS = str.maketrans("čćžšđČĆŽŠĐ", "cczsdCCZSD")
_KEY_WORDS = 5


def _norm(description: str) -> str:
    s = (description or "").translate(_DIACRITICS).lower()
    return re.sub(r"\s+", " ", s).strip()


def _significant_words(norm: str, limit: int | None = _KEY_WORDS) -> list[str]:
    words = [w for w in norm.split() if len(w) > 2]
    return words[:limit] if limit is not None else words


def _stem(word: str) -> str:
    # ponytail: Croatian declension means "restoranu"/"restoran" share a
    # prefix but not full equality — a 5-char prefix is enough to bridge
    # common suffix variants without pulling in a real stemmer/dependency.
    return word[:5]


def record_correction(spine, user: str, description: str, original_konto: str,
                       corrected_konto: str) -> int:
    norm = _norm(description)
    with spine.write() as c:
        cur = c.execute(
            """INSERT INTO konto_corrections(user, description, description_norm,
               original_konto, corrected_konto) VALUES(?,?,?,?,?)""",
            (user, description, norm, original_konto, corrected_konto),
        )
        correction_id = cur.lastrowid
    spine.audit(user, "konto_correction", corrected_konto,
                f"{description[:80]} : {original_konto} -> {corrected_konto}")
    return correction_id


def suggest_from_feedback(spine, description: str) -> dict | None:
    words = _significant_words(_norm(description))
    if not words:
        return None
    stems = {_stem(w) for w in words}

    like_clauses = " OR ".join(["description_norm LIKE ?"] * len(stems))
    params = [f"%{s}%" for s in stems]
    rows = spine.read().execute(
        f"SELECT description_norm, corrected_konto FROM konto_corrections WHERE {like_clauses}",
        params,
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        row_stems = {_stem(w) for w in _significant_words(row["description_norm"], limit=None)}
        if row_stems & stems:
            counts[row["corrected_konto"]] = counts.get(row["corrected_konto"], 0) + 1

    if not counts:
        return None

    konto = max(counts, key=counts.get)
    count = counts[konto]
    confidence = min(0.95, 0.7 + count * 0.05)
    return {"konto": konto, "confidence": confidence, "source": "naučeno", "count": count}
