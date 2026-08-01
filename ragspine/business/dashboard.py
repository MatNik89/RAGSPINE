# Agregatni brojčani pregled za početnu stranicu.

from ragspine.business import kalendar


def stats(spine) -> dict:
    active_clients = spine.read().execute(
        "SELECT COUNT(*) AS n FROM clients WHERE active=1"
    ).fetchone()["n"]
    deadlines_this_week = len(kalendar.upcoming(spine, days=7))
    # ponytail: interactions nema client_id (nema atribucije klijentu), pa
    # "top klijenti" računamo po broju bilješki — jedini dostupan signal.
    # Upgrade path: dodati clients.id atribuciju na interactions kad zatreba.
    top_rows = spine.read().execute(
        """SELECT c.name AS name, COUNT(*) AS cnt FROM notes n
           JOIN clients c ON c.id = n.client_id
           GROUP BY c.id ORDER BY cnt DESC, c.name LIMIT 5"""
    ).fetchall()
    unseen_notifications = spine.read().execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE seen=0"
    ).fetchone()["n"]
    return {
        "active_clients": active_clients,
        "deadlines_this_week": deadlines_this_week,
        "top_clients": [(r["name"], r["cnt"]) for r in top_rows],
        "unseen_notifications": unseen_notifications,
    }
