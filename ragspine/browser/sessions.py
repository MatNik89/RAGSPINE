"""Login session modes per host, with auto-learning from repeated failures."""

FAILURE_THRESHOLD = 2


def mode(spine, host: str) -> str:
    row = spine.read().execute(
        "SELECT mode FROM browser_sessions WHERE host=?", (host,)
    ).fetchone()
    return row["mode"] if row else "auto"


def set_mode(spine, host: str, mode_: str) -> None:
    with spine.write() as c:
        c.execute(
            "INSERT INTO browser_sessions(host, mode) VALUES(?, ?) "
            "ON CONFLICT(host) DO UPDATE SET mode=excluded.mode",
            (host, mode_),
        )


def record_failure(spine, host: str) -> str:
    with spine.write() as c:
        c.execute(
            "INSERT INTO browser_sessions(host, mode, failures) VALUES(?, 'auto', 1) "
            "ON CONFLICT(host) DO UPDATE SET failures=failures+1",
            (host,),
        )
        row = c.execute(
            "SELECT mode, failures FROM browser_sessions WHERE host=?", (host,)
        ).fetchone()
        new_mode = row["mode"]
        if row["failures"] >= FAILURE_THRESHOLD and new_mode != "keep":
            new_mode = "keep"
            c.execute("UPDATE browser_sessions SET mode='keep' WHERE host=?", (host,))
            c.execute(
                "INSERT INTO notifications(kind, body) VALUES(?, ?)",
                ("session_mode", f"{host} → keep"),
            )
    return new_mode


def record_success(spine, host: str) -> None:
    with spine.write() as c:
        c.execute(
            "INSERT INTO browser_sessions(host, mode, failures) VALUES(?, 'auto', 0) "
            "ON CONFLICT(host) DO UPDATE SET failures=0",
            (host,),
        )
