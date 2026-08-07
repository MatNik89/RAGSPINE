"""Trajno stanje setup wizarda u config_overrides(module='setup').
Omogućuje resume: pad usred wizarda -> nastavak od zadnjeg dovršenog koraka."""

_MOD = "setup"


def get_stage(spine) -> int:
    v = spine.get_override(_MOD, "stage")
    try:
        return int(v) if v is not None else 0
    except ValueError:
        return 0


def set_stage(spine, stage: int) -> None:
    spine.set_override(_MOD, "stage", str(int(stage)))


def is_complete(spine) -> bool:
    return spine.get_override(_MOD, "complete") == "true"


def mark_complete(spine) -> None:
    spine.set_override(_MOD, "complete", "true")


def reset(spine) -> None:
    with spine.write() as c:
        c.execute("DELETE FROM config_overrides WHERE module=?", (_MOD,))
