# Jutarnji pregled (morning digest) — per-worker Croatian text aggregating
# rokove, neposlane obveze, isteke dokumenata i zakonske promjene, dostavljeno
# apprise-om (ako je konfiguriran) ili spremljeno u notifications inače.
import logging
import re
from datetime import date

from ragspine.business import expiry, kalendar
from ragspine.core import optional
from ragspine.web.watchlist import INDUSTRY_KEYWORDS

logger = logging.getLogger(__name__)

# Poznate djelatnosti = ključevi INDUSTRY_KEYWORDS. Kategorija watch-izvora
# (npr. "ugostiteljstvo-turizam", "trgovina-proizvodnja-it") nosi industry-tokene;
# univerzalni izvori (porezna-vijesti, nn-*, place-statistika, doprinosi-hzmo)
# nemaju nijedan industry-token pa se prikazuju svima.
KNOWN_INDUSTRIES = set(INDUSTRY_KEYWORDS)
_DIA = str.maketrans("čćžšđ", "cczsd")
_CAT_RE = re.compile(r"^\[([^\]]+)\]")


def _norm(s: str) -> str:
    return (s or "").strip().lower().translate(_DIA)


def _cat_industries(body: str) -> set:
    """Industry-tokeni iz [category] prefiksa notifikacije; prazan set = univerzalno."""
    m = _CAT_RE.match(body or "")
    if not m:
        return set()
    tokens = {_norm(t) for t in m.group(1).split("-")}
    return tokens & KNOWN_INDUSTRIES


def _worker_client_ids(spine, worker: str) -> set:
    rows = spine.read().execute(
        "SELECT id FROM clients WHERE owner=?", (worker,)
    ).fetchall()
    return {r["id"] for r in rows}


def _unsent(spine, period: str, worker: str | None) -> list:
    q = """SELECT o.kind AS kind, c.name AS client
           FROM obligations o JOIN clients c ON c.id = o.client_id
           LEFT JOIN obligation_status s ON s.obligation_id = o.id
           WHERE o.period = ? AND COALESCE(s.sent, 0) = 0"""
    params: list = [period]
    if worker:
        q += " AND c.owner = ?"
        params.append(worker)
    q += " ORDER BY c.name COLLATE NOCASE"
    return spine.read().execute(q, params).fetchall()


def _worker_industries(spine, worker: str) -> set:
    rows = spine.read().execute(
        "SELECT DISTINCT industry FROM clients WHERE owner=? AND active=1", (worker,)
    ).fetchall()
    return {_norm(r["industry"]) for r in rows if r["industry"]}


def _law_changes(spine, worker: str | None = None) -> list:
    rows = spine.read().execute(
        """SELECT body FROM notifications
           WHERE kind IN ('law_change','rss') AND seen = 0
           ORDER BY at DESC LIMIT 40"""
    ).fetchall()
    if not worker:
        return rows[:20]  # office-wide: sve
    mine = _worker_industries(spine, worker)
    out = []
    for r in rows:
        inds = _cat_industries(r["body"])
        # univerzalni izvor (bez industry-tokena) → svima; sektorski → samo ako
        # radnik ima klijenta u toj djelatnosti
        if not inds or (inds & mine):
            out.append(r)
        if len(out) >= 20:
            break
    return out


def _eracun_count(spine) -> int:
    row = spine.read().execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE kind='eracun' AND at >= datetime('now','-1 day')"
    ).fetchone()
    return row["n"]


def build_digest(spine, cfg, worker: str | None = None, now_fn=None) -> str:
    today = (now_fn or date.today)()
    period = today.strftime("%Y-%m")

    deadlines = kalendar.upcoming(spine, days=7)
    unsent = _unsent(spine, period, worker)
    expiring = expiry.expiring(spine, days=30)
    if worker:
        ids = _worker_client_ids(spine, worker)
        expiring = [r for r in expiring if r["client_id"] in ids]
    law_changes = _law_changes(spine, worker)
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


def workers(spine) -> list:
    rows = spine.read().execute("SELECT username FROM users ORDER BY username").fetchall()
    return [r["username"] for r in rows]


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
    targets = workers(spine) or [None]
    for worker in targets:
        text = build_digest(spine, cfg, worker=worker)
        status = deliver(cfg, "RAGSPINE jutarnji pregled", text)
        if status != "apprise":
            with spine.write() as c:
                c.execute(
                    "INSERT INTO notifications(kind, body) VALUES(?,?)",
                    ("digest", text),
                )
