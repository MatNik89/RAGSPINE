"""Feature request tracking."""
import unicodedata

# Croatian low-confidence markers — an answer containing one of these is a
# signal the assistant hit a capability gap, not just a hard question.
LOWCONF_PHRASES = [
    "ne znam",
    "nisam siguran",
    "izvan moje domene",
    "nemam izvor",
    "ne mogu odgovoriti",
]


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def detect_missing_tool(answer: str, confidence: float, threshold: float = 0.3) -> bool:
    """True if `answer` looks like a capability gap: low confidence or a
    Croatian low-confidence phrase (diacritic-insensitive)."""
    if confidence < threshold:
        return True
    folded = _fold(answer)
    return any(phrase in folded for phrase in LOWCONF_PHRASES)


def maybe_file_gap(spine, user: str, query: str, answer: str, confidence: float):
    """Best-effort: auto-file a feature_request for a detected capability gap.
    Returns the new row id, or None if not filed (not a gap, or already
    filed — deduped on the query snippet)."""
    if not detect_missing_tool(answer, confidence):
        return None
    snippet = query[:150]
    existing = spine.read().execute(
        "SELECT id FROM feature_requests WHERE category='capability-gap' AND body LIKE ?",
        (f"%{snippet}%",),
    ).fetchone()
    if existing:
        return None
    body = f"Auto: nisko povjerenje na upit: {snippet}"
    return add(spine, user, body, priority=2, category="capability-gap")


def add(spine, user: str, body: str, priority: int = 3, category: str = "") -> int:
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO feature_requests(user,body,priority,category) VALUES(?,?,?,?)",
            (user, body, priority, category),
        )
        return cur.lastrowid


def list_open(spine):
    return spine.read().execute(
        "SELECT * FROM feature_requests ORDER BY priority ASC, at DESC"
    ).fetchall()
