"""Q&A knowledge base with difflib fuzzy lookup."""
import re
from difflib import SequenceMatcher


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def save(spine, question: str, answer: str, category: str = "", tags: str = "",
         org_id=None) -> int:
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO knowledge(question,answer,category,tags,org_id) VALUES(?,?,?,?,?)",
            (question, answer, category, tags, org_id),
        )
        return cur.lastrowid


def lookup(spine, question: str, threshold: float = 0.6, org_id=None) -> str | None:
    # ponytail: O(n) scan over all rows — fine for v1 KB size; upgrade path
    # is FTS5 prefilter (or embedding similarity) once the table grows large.
    norm_q = _norm(question)
    if org_id is None:  # legacy/CLI put — globalni pogled
        rows = spine.read().execute("SELECT id, question, answer FROM knowledge").fetchall()
    else:
        rows = spine.read().execute(
            "SELECT id, question, answer FROM knowledge WHERE org_id=?", (org_id,)).fetchall()
    best_row, best_ratio = None, 0.0
    for row in rows:
        ratio = SequenceMatcher(None, norm_q, _norm(row["question"])).ratio()
        if ratio > best_ratio:
            best_row, best_ratio = row, ratio
    if best_row is not None and best_ratio >= threshold:
        with spine.write() as c:
            c.execute("UPDATE knowledge SET hits = hits + 1 WHERE id=?", (best_row["id"],))
        return best_row["answer"]
    return None


def list_all(spine, category: str | None = None):
    if category is None:
        return spine.read().execute("SELECT * FROM knowledge ORDER BY id").fetchall()
    return spine.read().execute(
        "SELECT * FROM knowledge WHERE category=? ORDER BY id", (category,)
    ).fetchall()
