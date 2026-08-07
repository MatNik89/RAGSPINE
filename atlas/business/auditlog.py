# Pretraga audit traga.


def search(spine, client: str | None = None, user: str | None = None,
           action: str | None = None, limit: int = 100,
           org_id: int | None = None) -> list:
    """org_id = tvrdi org-filtar (audit_log nema org stupac, ali svaki redak
    nosi username aktera): admin vidi SAMO akcije članova svoje organizacije,
    preko subquery joina memberships→users (bez placeholder-po-članu — IN lista
    puca na SQLite limitu varijabli kod velikih orgova). Redci sistemskih
    aktera ('system', '?') time ispadaju — leak-safe smjer."""
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list = []
    if org_id is not None:
        sql += (" AND user IN (SELECT u.username FROM memberships m"
                " JOIN users u ON u.id = m.user_id WHERE m.org_id=?)")
        args.append(org_id)
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
