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


def send_expiry_reminder(spine, cfg, item_id: int, dry_run: bool = False) -> dict:
    """Pošalji klijentu podsjetnik da dokument ističe (uz PRISTANAK — messaging
    to gata). Vrati ishod slanja (status). Nepoznat item -> ValueError."""
    row = spine.read().execute(
        "SELECT client_id, label, expires FROM expiry_items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise ValueError(f"nepoznata stavka isteka: {item_id}")
    from atlas.business import messaging
    subject = f"Podsjetnik: {row['label']} ističe {row['expires']}"
    body = (f"Poštovani,\n\nvaš dokument „{row['label']}“ ističe {row['expires']}. "
            f"Molimo obnovite ga na vrijeme.\n\nRačunovodstveni ured")
    return messaging.send_to_client(spine, cfg, row["client_id"], subject, body, dry_run=dry_run)


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
