# Mjesečne obveze po klijentu (PDV/JOPPD/DOH) — tko je poslan/nije poslan.

KINDS = ("PDV", "JOPPD", "DOH")


def ensure_period(spine, kind: str, period: str) -> None:
    """Kreira obligations red za svakog aktivnog klijenta koji tu obvezu ima.
    Idempotentno (INSERT OR IGNORE + UNIQUE(client_id,kind,period))."""
    if kind == "PDV":
        where = "active=1 AND pdv_status LIKE '%u sustavu%' COLLATE NOCASE"
    elif kind in ("JOPPD", "DOH"):
        # ponytail: nema tablice zaposlenika pa JOPPD/DOH = svi aktivni klijenti.
        # Upgrade path: kad postoji clients.has_employees, filtrirati JOPPD na njih.
        where = "active=1"
    else:
        raise ValueError(f"Nepoznat kind obveze: {kind!r}")

    with spine.write() as c:
        for row in c.execute(f"SELECT id FROM clients WHERE {where}").fetchall():
            c.execute(
                "INSERT OR IGNORE INTO obligations(client_id, kind, period) VALUES(?,?,?)",
                (row["id"], kind, period),
            )


def list_period(spine, kind: str, period: str) -> list[dict]:
    rows = spine.read().execute(
        """SELECT o.id AS obligation_id, c.name AS client,
                  COALESCE(s.sent, 0) AS sent, s.sent_by AS sent_by, s.sent_at AS sent_at
           FROM obligations o
           JOIN clients c ON c.id = o.client_id
           LEFT JOIN obligation_status s ON s.obligation_id = o.id
           WHERE o.kind = ? AND o.period = ?
           ORDER BY sent, c.name COLLATE NOCASE""",
        (kind, period),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(spine, obligation_id: int, user: str, sent: bool = True) -> None:
    with spine.write() as c:
        c.execute(
            """INSERT INTO obligation_status(obligation_id, sent, sent_by, sent_at)
               VALUES(?,?,?,datetime('now'))
               ON CONFLICT(obligation_id) DO UPDATE SET
                 sent=excluded.sent, sent_by=excluded.sent_by, sent_at=excluded.sent_at""",
            (obligation_id, int(sent), user),
        )
    spine.audit(user, "obligation_sent" if sent else "obligation_unsent", f"obligation:{obligation_id}")
