# Completeness of a client's file — how much data/documentation is missing.

FIELDS = (
    ("oib", "OIB"),
    ("email", "email"),
    ("phone", "telefon"),
    ("owner", "vlasnik"),
    ("industry", "djelatnost"),
    ("pdv_status", "PDV status"),
)
FIELD_WEIGHT = 10
DOC_WEIGHT = 20


def score_client(spine, client_id: int) -> dict:
    row = spine.read().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if row is None:
        raise ValueError(f"nepoznat klijent: {client_id}")

    score = 0
    missing = []
    for col, label in FIELDS:
        if row[col] and str(row[col]).strip():
            score += FIELD_WEIGHT
        else:
            missing.append(label)

    doc_types = [d["doc_type"] for d in spine.read().execute(
        "SELECT doc_type FROM documents WHERE client_id=?", (client_id,)).fetchall()]
    has_ugovor = "ugovor" in doc_types
    has_other = any(t != "ugovor" for t in doc_types)

    if has_ugovor:
        score += DOC_WEIGHT
    else:
        missing.append("ugovor")
    if has_other:
        score += DOC_WEIGHT
    else:
        missing.append("izvod/dokument")

    return {"client_id": client_id, "score": score, "missing": missing, "client": row["name"]}


def worst_first(spine, visible=None) -> list[dict]:
    ids = [r["id"] for r in spine.read().execute(
        "SELECT id FROM clients WHERE active=1").fetchall()]
    if visible is not None:  # a restricted worker does not see other people's clients
        ids = [i for i in ids if i in visible]
    scored = [score_client(spine, cid) for cid in ids]
    scored.sort(key=lambda r: r["score"])
    return scored
