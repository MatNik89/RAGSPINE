# Odlazne poruke klijentima (WhatsApp/Telegram/mail preko apprise) —
# uvijek uz PRISTANAK klijenta (messaging_consent) i uvijek DRY-RUN dok se
# eksplicitno ne zatraži stvarno slanje. Zrcali sigurnosni obrazac iz
# ops/digest.py: nikad ne logirati str(e) ni apprise URL (mogu sadržavati
# kredencijale), samo type(e).__name__.
import logging
import socket

from atlas.business import expiry
from atlas.core import optional, security

logger = logging.getLogger(__name__)

# ponytail: known-safe apprise notification schemes — providers with their own
# fixed endpoint (mail relay, chat-bot API), not a generic webhook-to-anywhere.
# Operator can extend this set later if a new provider is needed.
ALLOWED_TARGET_SCHEMES = {
    # SAMO sheme s FIKSNIM provider-hostom. 'ntfy' izbačen: ntfy://<host>/topic
    # dopušta proizvoljan host (self-hosted) -> SSRF na loopback/LAN (Codex nalaz).
    "mailto", "mailtos", "tgram", "discord", "slack", "twilio", "pover", "pushover",
}


def _target_scheme_ok(target: str) -> bool:
    scheme = target.split("://", 1)[0].strip().lower() if "://" in target else ""
    return scheme in ALLOWED_TARGET_SCHEMES


def _mail_host_ok(target: str) -> bool:
    """Za mailto/mailtos: efektivni SMTP host ne smije biti interni/loopback.
    apprise dopušta 'mailto://user:pass@host?smtp=<drugi-host>' — bez ovoga bi
    autenticirani korisnik preusmjerio slanje na 169.254/127.0.0.1/LAN (SSRF;
    Codex nalaz). Ostale sheme imaju fiksni provider-host pa ne trebaju ovo."""
    import urllib.parse

    from atlas.core.net import _is_blocked_addr

    scheme = target.split("://", 1)[0].strip().lower()
    if scheme not in ("mailto", "mailtos"):
        return True
    p = urllib.parse.urlparse(target)
    qs = urllib.parse.parse_qs(p.query)
    host = (qs.get("smtp", [""])[0] or p.hostname or "").strip()
    if not host:
        return False  # nema odredišta -> ne šalji
    try:
        addrs = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return not any(_is_blocked_addr(sa[4][0]) for sa in addrs)


def render_message(subject: str, body: str) -> str:
    return f"{subject}\n\n{body}"


def _log(spine, client_id: int, channel: str, status: str, subject: str, body: str) -> None:
    # redact THEN truncate — truncating first can cut a PII token in half and
    # let the fragment through the regex unredacted.
    subject_r = security.redact_pii(subject)
    preview = security.redact_pii(body)[:120]
    with spine.write() as c:
        c.execute(
            "INSERT INTO message_log(client_id, channel, status, subject, body_preview) VALUES(?,?,?,?,?)",
            (client_id, channel, status, subject_r, preview),
        )


def send_to_client(spine, cfg, client_id: int, subject: str, body: str, dry_run: bool = True) -> dict:
    from atlas.business import secretbox
    row = spine.read().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    channel = row["messaging_channel"] if row else ""
    # target sadrži lozinku (mailto://user:pass@host) — u bazi je šifriran; dešifriraj
    # tek ovdje za korištenje. secretbox podnosi stare plaintext zapise (fallback).
    target = secretbox.decrypt(row["messaging_target"], cfg) if row else ""

    if row is None or not row["messaging_consent"] or not target:
        status = "skipped_no_consent"
        _log(spine, client_id, channel, status, subject, body)
        return {"status": status, "client_id": client_id}

    if not _target_scheme_ok(target):
        # SSRF guard: proizvoljna shema (http/json) natjerala bi apprise na izlaz
        # prema bilo kojem hostu (zaobilazi egress). Shema je jeftina, bez mreže.
        status = "skipped_bad_target"
        logger.warning("messaging target rejected: disallowed scheme (client %s)", client_id)
        _log(spine, client_id, channel, status, subject, body)
        return {"status": status, "client_id": client_id}

    if dry_run:
        status = "dry_run"
        _log(spine, client_id, channel, status, subject, body)
        return {"status": status, "client_id": client_id}

    if not _mail_host_ok(target):  # tek pred stvarno slanje (razrješava host -> mreža)
        # mailto sa smtp=interni-host / loopback = SSRF (Codex fold)
        status = "skipped_bad_target"
        logger.warning("messaging target rejected: internal SMTP host (client %s)", client_id)
        _log(spine, client_id, channel, status, subject, body)
        return {"status": status, "client_id": client_id}

    apprise_mod = optional.need("apprise", "client messaging")
    if apprise_mod is None:
        status = "failed"
    else:
        try:
            app = apprise_mod.Apprise()
            app.add(target.strip())
            ok = app.notify(title=subject, body=render_message(subject, body))
            status = "sent" if ok else "failed"
        except Exception as e:
            # ponytail: never log str(e) or the target — apprise exception text
            # can embed credentials (mailto://user:pass@host, tgram://token@...).
            logger.warning("messaging send failed (%s)", type(e).__name__)
            status = "failed"

    _log(spine, client_id, channel, status, subject, body)
    return {"status": status, "client_id": client_id}


def build_audience(spine, filt: str, **kw) -> list[int]:
    if filt == "compliance_missing":
        rows = spine.read().execute(
            """SELECT o.client_id AS client_id
               FROM obligations o
               LEFT JOIN obligation_status s ON s.obligation_id = o.id
               WHERE o.kind = ? AND o.period = ? AND COALESCE(s.sent, 0) = 0""",
            (kw["kind"], kw["period"]),
        ).fetchall()
        return [r["client_id"] for r in rows]

    if filt == "expiring_soon":
        rows = expiry.expiring(spine, days=kw.get("days", 30))
        seen: list[int] = []
        for r in rows:
            if r["client_id"] not in seen:
                seen.append(r["client_id"])
        return seen

    if filt == "all_active":
        rows = spine.read().execute("SELECT id FROM clients WHERE active=1").fetchall()
        return [r["id"] for r in rows]

    raise ValueError(f"nepoznat filter: {filt!r}")


def send_to_filter(spine, cfg, filt: str, subject: str, body: str, dry_run: bool = True, **kw) -> dict:
    audience = build_audience(spine, filt, **kw)
    counts: dict[str, int] = {}
    for client_id in audience:
        result = send_to_client(spine, cfg, client_id, subject, body, dry_run=dry_run)
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {"audience": len(audience), "results": counts}
