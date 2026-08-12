# Per-client notes journal.


def add(spine, client_id: int, author: str, body: str) -> int:
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO notes(client_id, author, body) VALUES(?,?,?)",
            (client_id, author, body),
        )
        note_id = cur.lastrowid
    spine.audit(author, "note_add", f"client:{client_id}", body[:80])
    return note_id


def search(spine, term: str | None = None, client_id: int | None = None) -> list:
    sql = """SELECT n.id, n.client_id, n.author, n.body, n.created_at, c.name
             FROM notes n JOIN clients c ON c.id = n.client_id WHERE 1=1"""
    args: list = []
    if client_id is not None:
        sql += " AND n.client_id=?"
        args.append(client_id)
    if term:
        sql += " AND n.body LIKE ?"
        args.append(f"%{term}%")
    sql += " ORDER BY n.created_at DESC"
    return spine.read().execute(sql, args).fetchall()
