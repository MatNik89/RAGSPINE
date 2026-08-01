"""Detect repeated query patterns across interactions → skill suggestions."""
import json
import re


def normalize(query: str) -> str:
    q = re.sub(r"\d+", "#", query.strip().lower())
    return re.sub(r"\s+", " ", q).strip()


def detect(spine, min_count: int = 5) -> list[dict]:
    rows = spine.read().execute("SELECT query FROM interactions").fetchall()
    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(normalize(row["query"]), []).append(row["query"])

    result = []
    for pattern, queries in groups.items():
        count = len(queries)
        if count < min_count:
            continue
        params = sorted(set(queries))
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
