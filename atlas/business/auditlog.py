# Audit trail search.


def search(spine, client: str | None = None, user: str | None = None,
           action: str | None = None, limit: int = 100,
           org_id: int | None = None) -> list:
    """org_id = a hard org filter (audit_log has no org column, but every row
    carries the actor's username): an admin sees ONLY the actions of members of
    their own organization, via a subquery join memberships->users (no
    placeholder-per-member -- an IN list breaks on SQLite's variable limit for
    large orgs). Rows of system actors ('system', '?') fall out this way -- a
    leak-safe direction."""
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
