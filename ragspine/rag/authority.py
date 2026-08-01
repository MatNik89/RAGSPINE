"""Source authority weighting + inline Croatian legal-citation extraction.

Ranks retrieval hits by legal authority (Zakon > Pravilnik > ... > interna
procedura) so answers grounded in stronger sources score higher confidence,
and mines inline legal references ("članak 85. Zakona o PDV-u") into the
knowledge graph so related documents can be surfaced.
"""
import re
import unicodedata

AUTHORITY = {
    "zakon": 1.0,
    "pravilnik": 0.95,
    "uredba": 0.9,
    "kolektivni_ugovor": 0.9,
    "misljenje_porezna": 0.85,
    "nn_objava": 0.8,
    "strukovno": 0.75,
    "interna_procedura": 0.7,
    "default": 0.5,
}


def _fold(s: str) -> str:
    """Lowercase + strip diacritics (č/ć/š/đ/ž -> c/c/s/d/z)."""
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def detect_authority(title: str, path: str = "", doc_type: str = "") -> tuple[str, float]:
    hay = _fold(f"{title} {path} {doc_type} ")
    if "zakon o" in hay or "zakon " in hay:
        tier = "zakon"
    elif "pravilnik" in hay:
        tier = "pravilnik"
    elif "uredba" in hay:
        tier = "uredba"
    elif "kolektivni ugovor" in hay:
        tier = "kolektivni_ugovor"
    elif "misljenje" in hay and ("porezna" in hay or "porezne uprave" in hay):
        tier = "misljenje_porezna"
    elif "narodne novine" in hay or "nn " in hay:
        tier = "nn_objava"
    elif "hrvatska zajednica" in hay or "komor" in hay or "strukovn" in hay:
        tier = "strukovno"
    elif "sop" in hay or "interna" in hay or "procedura" in hay:
        tier = "interna_procedura"
    else:
        tier = "default"
    return tier, AUTHORITY[tier]


def authority_bonus(hits) -> float:
    """Max authority weight across hit titles; 0.5 (default) if no hits."""
    if not hits:
        return 0.5
    return max(detect_authority(h.title, doc_type=h.doc_type)[1] for h in hits)


def blend_authority(base_confidence: float, hits) -> float:
    """Fold authority into a citation-verified confidence score, bounded [0,1]."""
    blended = base_confidence * 0.7 + authority_bonus(hits) * 0.3
    return max(0.0, min(1.0, blended))


# --- inline legal-citation extraction ---
# Law-name capture is a single token (no whitespace/punctuation): covers the
# "Zakon o PDV-u" style references this domain actually uses, and keeps the
# regex a single negated-char-class `+` (linear time, no ReDoS).
# ponytail: multi-word law names ("Zakon o porezu na dohodak") aren't split
# out — upgrade to a bounded word-count capture if that's ever needed.
_TOKEN = r"([^\s.,;()\n]+)"
_CLANAK_RE = re.compile(
    r"čl(?:anak|anku|anka|\.)\s*(\d+)\.?\s+zakona\s+o\s+" + _TOKEN, re.IGNORECASE,
)
_ZAKON_RE = re.compile(r"\bzakon[au]?\s+o\s+" + _TOKEN, re.IGNORECASE)
_PRAVILNIK_RE = re.compile(r"\bpravilnik[au]?\s+o\s+" + _TOKEN, re.IGNORECASE)
_NN_RE = re.compile(r"\bNN\s+(\d+)/(\d{4})\b")

REFERENCE_PATTERNS = {
    "clanak": _CLANAK_RE,
    "zakon": _ZAKON_RE,
    "pravilnik": _PRAVILNIK_RE,
    "nn": _NN_RE,
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _overlaps(span, spans) -> bool:
    s, e = span
    return any(not (e <= cs or s >= ce) for cs, ce in spans)


def extract_references(text: str) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    consumed: list[tuple[int, int]] = []

    for m in _CLANAK_RE.finditer(text):
        consumed.append(m.span())
        article = int(m.group(1))
        value = f"članak {article} Zakona o {_norm(m.group(2))}"
        key = ("clanak", value.lower())
        if key not in seen:
            seen.add(key)
            out.append({"kind": "clanak", "value": value, "article": article})

    for m in _ZAKON_RE.finditer(text):
        if _overlaps(m.span(), consumed):
            continue
        value = f"Zakon o {_norm(m.group(1))}"
        key = ("zakon", value.lower())
        if key not in seen:
            seen.add(key)
            out.append({"kind": "zakon", "value": value, "article": None})

    for m in _PRAVILNIK_RE.finditer(text):
        value = f"Pravilnik o {_norm(m.group(1))}"
        key = ("pravilnik", value.lower())
        if key not in seen:
            seen.add(key)
            out.append({"kind": "pravilnik", "value": value, "article": None})

    for m in _NN_RE.finditer(text):
        value = f"NN {m.group(1)}/{m.group(2)}"
        key = ("nn", value.lower())
        if key not in seen:
            seen.add(key)
            out.append({"kind": "nn", "value": value, "article": None})

    return out


# --- knowledge-graph wiring ---

def _node_id(c, kind: str, value: str) -> int:
    c.execute("INSERT OR IGNORE INTO kg_nodes(kind,value) VALUES(?,?)", (kind, value))
    return c.execute(
        "SELECT id FROM kg_nodes WHERE kind=? AND value=?", (kind, value)
    ).fetchone()["id"]


def index_references(spine, doc_id, text: str) -> int:
    """Extract inline legal refs from text, link doc -> reference via 'cites' edges."""
    refs = extract_references(text)
    if not refs:
        return 0
    with spine.write() as c:
        doc_node = _node_id(c, "doc", str(doc_id))
        for ref in refs:
            ref_node = _node_id(c, ref["kind"], ref["value"])
            c.execute(
                "INSERT OR IGNORE INTO kg_edges(src,dst,rel,doc_id) VALUES(?,?,?,?)",
                (doc_node, ref_node, "cites", doc_id),
            )
    return len(refs)


def related_documents(spine, hits, limit: int = 5) -> list[dict]:
    """Other documents that cite the same legal references as the hit docs."""
    doc_ids = {h.doc_id for h in hits}
    if not doc_ids:
        return []
    conn = spine.read()

    ph = ",".join("?" * len(doc_ids))
    own_ids = [
        r["id"] for r in conn.execute(
            f"SELECT id FROM kg_nodes WHERE kind='doc' AND value IN ({ph})",
            tuple(str(d) for d in doc_ids),
        ).fetchall()
    ]
    if not own_ids:
        return []

    ph2 = ",".join("?" * len(own_ids))
    ref_ids = {
        r["dst"] for r in conn.execute(
            f"SELECT DISTINCT dst FROM kg_edges WHERE rel='cites' AND src IN ({ph2})",
            tuple(own_ids),
        ).fetchall()
    }
    if not ref_ids:
        return []

    ph3 = ",".join("?" * len(ref_ids))
    other_node_ids = {
        r["src"] for r in conn.execute(
            f"SELECT DISTINCT src FROM kg_edges WHERE rel='cites' AND dst IN ({ph3})",
            tuple(ref_ids),
        ).fetchall()
        if r["src"] not in own_ids
    }
    if not other_node_ids:
        return []

    ph4 = ",".join("?" * len(other_node_ids))
    other_doc_ids = [
        int(r["value"]) for r in conn.execute(
            f"SELECT value FROM kg_nodes WHERE id IN ({ph4})", tuple(other_node_ids),
        ).fetchall()
        if int(r["value"]) not in doc_ids
    ][:limit]
    if not other_doc_ids:
        return []

    ph5 = ",".join("?" * len(other_doc_ids))
    rows = conn.execute(
        f"SELECT id AS doc_id, title FROM documents WHERE id IN ({ph5})",
        tuple(other_doc_ids),
    ).fetchall()
    return [{"title": r["title"], "doc_id": r["doc_id"]} for r in rows][:limit]
