"""Feature request tracking."""


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
