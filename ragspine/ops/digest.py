# Jutarnji pregled (morning digest) — office-wide hrvatski tekst koji spaja
# rokove, neposlane obveze, isteke dokumenata i zakonske promjene za CIJELI ured.
# Svi radnici vide isto (jedan zajednički pregled). Dostavljeno apprise-om ako je
# konfiguriran, inače spremljeno u notifications.
import logging
from datetime import date

from ragspine.business import expiry, kalendar
from ragspine.core import optional

logger = logging.getLogger(__name__)


def _unsent(spine, period: str) -> list:
    return spine.read().execute(
        """SELECT o.kind AS kind, c.name AS client
           FROM obligations o JOIN clients c ON c.id = o.client_id
           LEFT JOIN obligation_status s ON s.obligation_id = o.id
           WHERE o.period = ? AND COALESCE(s.sent, 0) = 0
           ORDER BY c.name COLLATE NOCASE""",
        (period,),
    ).fetchall()


def _law_changes(spine) -> list:
    return spine.read().execute(
        """SELECT body FROM notifications
           WHERE kind IN ('law_change','rss') AND seen = 0
           ORDER BY at DESC LIMIT 20"""
    ).fetchall()


def _eracun_count(spine) -> int:
    row = spine.read().execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE kind='eracun' AND at >= datetime('now','-1 day')"
    ).fetchone()
    return row["n"]


def build_digest(spine, cfg, now_fn=None) -> str:
    today = (now_fn or date.today)()
    period = today.strftime("%Y-%m")

    deadlines = kalendar.upcoming(spine, days=7)
    unsent = _unsent(spine, period)
    expiring = expiry.expiring(spine, days=30)
    law_changes = _law_changes(spine)
    eracun_count = _eracun_count(spine)

    lines = [f"Jutarnji pregled — {today.isoformat()}", ""]

    lines.append(f"Rokovi u sljedećih 7 dana: {len(deadlines)}")
    for d in deadlines:
        lines.append(f"  - {d['description']} — rok {d['due']}")

    lines.append("")
    lines.append(f"{len(unsent)} neposlanih obveza ({period}):")
    for row in unsent:
        lines.append(f"  - {row['client']} ({row['kind']})")

    lines.append("")
    lines.append(f"Istek dokumenata u sljedećih 30 dana: {len(expiring)}")
    for row in expiring:
        lines.append(f"  - {row['label']} ({row['client_name']}) ističe {row['expires']}")

    lines.append("")
    lines.append(f"Nove zakonske promjene: {len(law_changes)}")
    for row in law_changes:
        lines.append(f"  - {row['body']}")

    if eracun_count:
        lines.append("")
        lines.append(f"Novi e-računi: {eracun_count}")

    if not (deadlines or unsent or expiring or law_changes or eracun_count):
        lines.append("")
        lines.append("Nema hitnih obveza danas.")

    return "\n".join(lines)


def deliver(cfg, subject: str, body: str) -> str:
    if not cfg.apprise_urls:
        return "none"
    apprise_mod = optional.need("apprise", "digest delivery")
    if apprise_mod is None:
        return "none"
    try:
        app = apprise_mod.Apprise()
        for url in cfg.apprise_urls:
            app.add(url.strip())
        ok = app.notify(title=subject, body=body)
        return "apprise" if ok else "error"
    except Exception as e:
        # ponytail: never log str(e) or the urls here — apprise exception text
        # can embed target credentials (mailto://user:pass@host, tgram://token@...).
        logger.warning("digest deliver failed (apprise, %s)", type(e).__name__)
        return "error"


def digest_job(spine, cfg) -> None:
    text = build_digest(spine, cfg)
    status = deliver(cfg, "RAGSPINE jutarnji pregled", text)
    if status != "apprise":
        with spine.write() as c:
            c.execute("INSERT INTO notifications(kind, body) VALUES(?,?)", ("digest", text))
