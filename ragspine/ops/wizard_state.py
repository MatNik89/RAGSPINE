"""Trajno stanje setup wizarda u config_overrides(module='setup').
Omogućuje resume: pad usred wizarda -> nastavak od zadnjeg dovršenog koraka."""

_MOD = "setup"


def _get(spine, key: str) -> str | None:
    r = spine.read().execute(
        "SELECT value FROM config_overrides WHERE module=? AND key=?", (_MOD, key)
    ).fetchone()
    return r["value"] if r else None


def _put(spine, key: str, value: str) -> None:
    with spine.write() as c:
        c.execute(
            """INSERT INTO config_overrides(module, key, value, updated_at)
               VALUES(?,?,?,datetime('now'))
               ON CONFLICT(module, key) DO UPDATE SET
                 value=excluded.value, updated_at=excluded.updated_at""",
            (_MOD, key, value),
        )


def get_stage(spine) -> int:
    v = _get(spine, "stage")
    try:
        return int(v) if v is not None else 0
    except ValueError:
        return 0


def set_stage(spine, stage: int) -> None:
    _put(spine, "stage", str(int(stage)))


def is_complete(spine) -> bool:
    return _get(spine, "complete") == "true"


def mark_complete(spine) -> None:
    _put(spine, "complete", "true")


def reset(spine) -> None:
    with spine.write() as c:
        c.execute("DELETE FROM config_overrides WHERE module=?", (_MOD,))
