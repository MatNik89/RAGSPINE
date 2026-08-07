"""Short-term conversation memory: replay a user's recent interactions as prior chat turns.

Prior turns are memory, not retrieved sources — composer/citations never see them.
"""

DEFAULT_LIMIT = 5
_ANSWER_CHARS = 500


def recent_turns(spine, user: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Last `limit` interactions for `user`, oldest first (chronological)."""
    rows = spine.read().execute(
        "SELECT query, answer FROM interactions WHERE user=? ORDER BY id DESC LIMIT ?",
        (user, limit),
    ).fetchall()
    return [{"query": r["query"], "answer": r["answer"]} for r in reversed(rows)]


def as_messages(turns: list[dict]) -> list[dict]:
    """Turns -> alternating user/assistant messages; answers capped to bound prompt size."""
    messages = []
    for t in turns:
        messages.append({"role": "user", "content": t["query"]})
        messages.append({"role": "assistant", "content": t["answer"][:_ANSWER_CHARS]})
    return messages
