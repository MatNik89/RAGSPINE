"""FSRS-lite memory decay: a spaced-repetition-style hotness score on the
`memory` table (user,key,value + hot_score/last_access/access_count). Fresh
writes and reads reinforce hotness; an unattended background job applies an
exponential forgetting curve so `hot_memories()` answers "what's still
relevant" without any ML.
"""
import math
from datetime import datetime

_MAX_HOT = 10.0
_TOUCH_BUMP = 0.1
_FLOOR = 0.01


def _now_iso(now_fn=None) -> str:
    now = (now_fn or datetime.now)()
    return now.isoformat(sep=" ")


def write_memory(spine, user: str, key: str, value: str, now_fn=None) -> None:
    """Upsert (user,key,value). A fresh write resets hotness to 1.0 and bumps
    access_count — writing counts as an access."""
    now = _now_iso(now_fn)
    with spine.write() as c:
        c.execute(
            """INSERT INTO memory(user, key, value, hot_score, last_access, access_count)
               VALUES(?,?,?,1.0,?,1)
               ON CONFLICT(user,key) DO UPDATE SET
                 value=excluded.value, hot_score=1.0, last_access=excluded.last_access,
                 access_count=memory.access_count+1""",
            (user, key, value, now),
        )


def touch_memory(spine, user: str, key: str, now_fn=None) -> None:
    """Recall reinforces: +0.1 hotness (capped), bump last_access/access_count."""
    now = _now_iso(now_fn)
    with spine.write() as c:
        c.execute(
            """UPDATE memory SET hot_score=MIN(?, hot_score+?), last_access=?,
                 access_count=access_count+1
               WHERE user=? AND key=?""",
            (_MAX_HOT, _TOUCH_BUMP, now, user, key),
        )


def get_memory(spine, user: str, key: str, now_fn=None) -> str | None:
    """Read value; accessing it reinforces hotness (touch)."""
    row = spine.read().execute(
        "SELECT value FROM memory WHERE user=? AND key=?", (user, key)
    ).fetchone()
    if row is None:
        return None
    touch_memory(spine, user, key, now_fn=now_fn)
    return row["value"]


def decay_all(spine, half_life_days: float = 14.0, now_fn=None) -> int:
    """Apply the forgetting curve to every memory row: hot_score decays
    exponentially with the given half-life since last_access. Floors at
    _FLOOR so a memory never hits zero/negative. Returns rows updated."""
    now = (now_fn or datetime.now)()
    rows = spine.read().execute(
        "SELECT id, hot_score, last_access FROM memory WHERE user != 'scheduler'"
    ).fetchall()
    updated = 0
    with spine.write() as c:
        for row in rows:
            last = datetime.fromisoformat(row["last_access"])
            days_since = max(0.0, (now - last).total_seconds() / 86400.0)
            new_score = row["hot_score"] * math.exp(-math.log(2) * days_since / half_life_days)
            new_score = max(_FLOOR, new_score)
            c.execute("UPDATE memory SET hot_score=? WHERE id=?", (new_score, row["id"]))
            updated += 1
    return updated


def hot_memories(spine, user: str, limit: int = 10, min_score: float = 0.0) -> list[dict]:
    """The user's memories sorted by hot_score DESC — the 'what's still
    relevant' view."""
    rows = spine.read().execute(
        """SELECT key, value, hot_score FROM memory
           WHERE user=? AND hot_score>=? ORDER BY hot_score DESC LIMIT ?""",
        (user, min_score, limit),
    ).fetchall()
    return [{"key": r["key"], "value": r["value"], "hot_score": r["hot_score"]} for r in rows]


def forget_cold(spine, threshold: float = 0.05) -> int:
    """Delete fully-faded memories (hot_score below threshold). Returns count
    deleted. Optional — call from the decay job if pruning is desired.
    Excludes user='scheduler' — that's the scheduler's own lastrun.{job}
    bookkeeping (see ops/scheduler.py), never user memory to forget."""
    with spine.write() as c:
        cur = c.execute(
            "DELETE FROM memory WHERE hot_score < ? AND user != 'scheduler'", (threshold,)
        )
        return cur.rowcount
