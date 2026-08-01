"""Clarify gate: ask instead of guessing when a how-to query is ambiguous.

A worker asking "kako se radi plaća" without saying the client or type
(obrt/paušal/poduzeće/...) gets a clarifying question INSTEAD of a guessed
answer, but only when genuinely ambiguous — i.e. ≥2 approved SOP variants
exist for the topic that differ by client or type. Diacritic-insensitive
throughout (reuses router._normalize).
"""
import re

from ragspine.rag.router import _normalize

HOWTO_RE = re.compile(
    r"kako\s+(se\s+)?radi"
    r"|kako\s+(se\s+)?(knjizi|napravi|izradi|obracuna)"
    r"|postupak\s+za"
    r"|koraci\s+za",
    re.IGNORECASE,
)

# Disambiguator keywords, most-specific first so e.g. "pausalni obrt" wins
# over the bare "obrt" it contains.
TYPE_KEYWORDS = [
    "j.d.o.o.", "jdoo", "trgovacko drustvo",
    "pausalni obrt", "pausal",
    "poduzece",
    "d.o.o.", "doo",
    "obrt",
    "udruga",
]

_STRIP_RE = re.compile(
    r"^(kako\s+(se\s+)?(radi|knjizi|napravi|izradi|obracuna)\s*"
    r"|postupak\s+za\s*|koraci\s+za\s*)",
    re.IGNORECASE,
)
_STOPWORDS = {"za", "je", "li", "se", "sto"}


def is_howto(query: str) -> bool:
    return bool(HOWTO_RE.search(_normalize(query or "")))


def mentions_type(query: str) -> str | None:
    q = _normalize(query or "")
    for kw in TYPE_KEYWORDS:
        if kw in q:
            return kw
    return None


_PREFIX_MIN = 4  # min shared-prefix length to call two words "the same" across Croatian declension


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def mentions_client(spine, query: str) -> str | None:
    """Declension-robust: Croatian case endings change a name's tail
    ("Pekara" -> "za Pekaru"), so match on a >=4-char shared prefix between
    a significant (>=4 char) name word and a query word, not exact/substring
    equality. Short name words (e.g. "d.o.o.") are never enough alone."""
    q_words = re.findall(r"\w+", _normalize(query or ""))
    rows = spine.read().execute("SELECT name FROM clients").fetchall()
    for row in rows:
        name = row["name"]
        if not name:
            continue
        name_words = [w for w in re.findall(r"\w+", _normalize(name)) if len(w) >= _PREFIX_MIN]
        for nw in name_words:
            for qw in q_words:
                if len(qw) >= _PREFIX_MIN and _common_prefix_len(nw, qw) >= _PREFIX_MIN:
                    return name
    return None


def _topic_keywords(query: str) -> list[str]:
    q = _STRIP_RE.sub("", _normalize(query or "").strip()).strip()
    q = q.rstrip("?!. ")
    words = [w for w in re.findall(r"\w+", q) if w not in _STOPWORDS and len(w) >= 3]
    return words or ([q] if q else [])


def sop_variants(spine, topic_keywords: list[str]) -> list[dict]:
    """Approved SOPs whose title/category matches any topic keyword."""
    if not topic_keywords:
        return []
    rows = spine.read().execute(
        "SELECT id, title, client_id, category FROM sop_pages WHERE status='approved'"
    ).fetchall()
    out = []
    for row in rows:
        haystack = _normalize(f"{row['title'] or ''} {row['category'] or ''}")
        if any(kw in haystack for kw in topic_keywords):
            out.append({"sop_id": row["id"], "title": row["title"],
                        "client_id": row["client_id"], "category": row["category"]})
    return out


def _variant_label(spine, variant: dict) -> str:
    type_kw = mentions_type(f"{variant['title'] or ''} {variant['category'] or ''}")
    if type_kw:
        return type_kw
    if variant["client_id"] is not None:
        row = spine.read().execute(
            "SELECT name FROM clients WHERE id=?", (variant["client_id"],)
        ).fetchone()
        if row and row["name"]:
            return row["name"]
    return variant["category"] or variant["title"] or "?"


def needs_clarification(spine, query: str) -> dict | None:
    """None if the query is specific enough already, or if there's nothing
    to disambiguate (0/1 variant). Otherwise a dict with a concrete HR
    clarifying question and the distinct variants found."""
    if not is_howto(query):
        return None
    if mentions_client(spine, query) is not None:
        return None
    if mentions_type(query) is not None:
        return None

    variants = sop_variants(spine, _topic_keywords(query))

    seen = set()
    distinct = []
    for v in variants:
        key = (v["client_id"], v["category"])
        if key in seen:
            continue
        seen.add(key)
        distinct.append(v)

    if len(distinct) < 2:
        return None

    topic = (_topic_keywords(query) or ["ovo"])[0]
    labels = [_variant_label(spine, v) for v in distinct]
    question = (
        f"Postoji više varijanti za '{topic}'. "
        f"Za kojeg klijenta ili koji tip radiš: {', '.join(labels)}?"
    )
    return {"question": question, "variants": distinct}
