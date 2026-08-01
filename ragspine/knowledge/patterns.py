"""Detect repeated query patterns across interactions → skill suggestions.

Clustering is keyword-Jaccard based (not exact-normalized-string match): two
differently-worded queries ("koliki je prirez za Split" / "prirez Split")
group together when they share enough domain keywords, so a real repeated
capability gap isn't missed just because users phrase it differently.
"""
import json
import re
import unicodedata

JACCARD_THRESHOLD = 0.5

# Croatian stopwords: question words, prepositions, conjunctions — noise for
# keyword clustering. ponytail: small hand-picked set, not a full stopword
# list/lemmatizer; upgrade if false-negatives show up on real query logs.
STOPWORDS = {
    "za", "je", "u", "na", "koliki", "koliko", "kolika", "koje", "koji", "koja",
    "se", "i", "li", "su", "sam", "ti", "mi", "ne", "da", "o", "s", "sa",
    "od", "do", "kod", "po", "a",
}


def normalize(query: str) -> str:
    q = re.sub(r"\d+", "#", query.strip().lower())
    return re.sub(r"\s+", " ", q).strip()


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _keywords(query: str) -> set:
    """Domain-keyword set: lowercased, diacritic-stripped, stopwords dropped."""
    tokens = re.findall(r"[a-z0-9]+", _fold(query))
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect(spine, min_count: int = 5) -> list[dict]:
    rows = spine.read().execute("SELECT query FROM interactions").fetchall()

    # Greedy single-pass clustering: each query joins the first existing
    # cluster whose representative (first member's) keyword set is similar
    # enough, else starts a new cluster. Deterministic given fixed input order.
    clusters: list[dict] = []
    for row in rows:
        query = row["query"]
        kw = _keywords(query)
        cluster = next(
            (c for c in clusters if _jaccard(kw, c["keywords"]) > JACCARD_THRESHOLD),
            None,
        )
        if cluster is None:
            clusters.append({"keywords": kw, "queries": [query]})
        else:
            cluster["queries"].append(query)

    result = []
    for cluster in clusters:
        count = len(cluster["queries"])
        if count < min_count:
            continue
        distinct = sorted(set(cluster["queries"]))
        pattern = distinct[0]
        params = distinct
        with spine.write() as c:
            existing = c.execute(
                "SELECT id FROM skill_suggestions WHERE pattern=?", (pattern,)
            ).fetchone()
            if existing:
                c.execute(
                    "UPDATE skill_suggestions SET count=?, params=? WHERE id=?",
                    (count, json.dumps(params), existing["id"]),
                )
            else:
                c.execute(
                    "INSERT INTO skill_suggestions(pattern,count,params) VALUES(?,?,?)",
                    (pattern, count, json.dumps(params)),
                )
        result.append({"pattern": pattern, "count": count})
    return result
