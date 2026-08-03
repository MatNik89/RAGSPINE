# Pretraga audit traga.


def search(spine, client: str | None = None, user: str | None = None,
           action: str | None = None, limit: int = 100,
           usernames: list[str] | None = None) -> list:
    """usernames = tvrdi org-filtar (audit_log nema org stupac, ali svaki redak
    nosi username aktera): admin vidi SAMO akcije članova svoje organizacije.
    Redci sistemskih aktera ('system', '?') time ispadaju — leak-safe smjer."""
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list = []
    if usernames is not None:
        if not usernames:
            return []
        sql += f" AND user IN ({','.join('?' * len(usernames))})"
        args.extend(usernames)
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
