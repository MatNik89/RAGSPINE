# Praćenje isteka dokumenata po klijentu (osobne, dozvole, certifikati...).

from datetime import date, timedelta


def add(spine, client_id: int, kind: str, label: str, expires: str) -> int:
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO expiry_items(client_id, kind, label, expires) VALUES(?,?,?,?)",
            (client_id, kind, label, expires),
        )
        return cur.lastrowid


def _today() -> date:
    return date.today()


def expiring(spine, days: int = 60) -> list:
    today = _today()
    end = today + timedelta(days=days)
    return spine.read().execute(
        """SELECT e.id, e.client_id, e.kind, e.label, e.expires, c.name AS client_name
           FROM expiry_items e
           JOIN clients c ON c.id = e.client_id
           WHERE e.expires BETWEEN ? AND ?
           ORDER BY e.expires""",
        (today.isoformat(), end.isoformat()),
    ).fetchall()
