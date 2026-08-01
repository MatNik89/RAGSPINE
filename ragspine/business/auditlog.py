# Pretraga audit traga.


def search(spine, client: str | None = None, user: str | None = None,
           action: str | None = None, limit: int = 100) -> list:
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list = []
    if user:
        sql += " AND user=?"
        args.append(user)
    if action:
        sql += " AND action=?"
        args.append(action)
    if client:
        sql += " AND (entity LIKE ? OR detail LIKE ?)"
        args.extend([f"%{client}%", f"%{client}%"])
    sql += " ORDER BY at DESC LIMIT ?"
    args.append(limit)
    return spine.read().execute(sql, args).fetchall()
