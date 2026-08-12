# W3: client-scoped answer enrichment. When a worker's query names a
# specific client (declension-robust detection reused from clarify.W2),
# surface that client's own approved SOPs + recent notes as a short
# "Napomena za klijenta X" (Note for client X) block appended to the normal answer.
from atlas.rag import clarify


def resolve_client(spine, query: str, actor=None) -> dict | None:
    name = clarify.mentions_client(spine, query)
    if name is None:
        return None
    row = spine.read().execute("SELECT * FROM clients WHERE name=?", (name,)).fetchone()
    if row is None:
        return None
    # a worker with restricted visibility must not receive notes/SOPs for a client
    # they are not allowed to see (otherwise they would leak through the chat note)
    if actor is not None:
        from atlas.business import client_visibility
        if not client_visibility.can_see(spine, actor.user_id, row["id"], actor.role):
            return None
    return dict(row)


def client_sops(spine, client_id: int, topic_query: str = "") -> list[dict]:
    """Approved SOPs for this client, topic keywords preferred first."""
    rows = spine.read().execute(
        "SELECT id, title, category, content FROM sop_pages WHERE client_id=? AND status='approved'",
        (client_id,),
    ).fetchall()
    keywords = clarify._topic_keywords(topic_query) if topic_query else []

    def _matches(row) -> bool:
        if not keywords:
            return True
        haystack = clarify._normalize(f"{row['title'] or ''} {row['category'] or ''}")
        return any(kw in haystack for kw in keywords)

    matched = [r for r in rows if _matches(r)]
    chosen = matched if matched else rows
    return [{"sop_id": r["id"], "title": r["title"], "category": r["category"],
              "content_snippet": (r["content"] or "")[:200]} for r in chosen]


def client_notes_recent(spine, client_id: int, limit: int = 5) -> list[dict]:
    rows = spine.read().execute(
        "SELECT body, author, created_at FROM notes WHERE client_id=? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (client_id, limit),
    ).fetchall()
    return [{"body": r["body"], "author": r["author"], "created_at": r["created_at"]} for r in rows]


def client_note_block(spine, client_id: int, client_name: str, topic_query: str = "") -> str:
    sops = client_sops(spine, client_id, topic_query)
    recent_notes = client_notes_recent(spine, client_id, limit=3)
    if not sops and not recent_notes:
        return ""

    lines = [f"📌 Napomena za klijenta {client_name}:"]
    for s in sops:
        lines.append(f"- Za ovog klijenta postoji uputa: {s['title']}")
    for n in recent_notes:
        lines.append(f"- Nedavna bilješka: {n['body']}")
    return "\n".join(lines)
