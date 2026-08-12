# Outbound messages to clients (WhatsApp/Telegram/mail via apprise) — always with
# client CONSENT (messaging_consent) and always DRY-RUN until a real send is
# explicitly requested. Mirrors the security pattern from ops/digest.py: never log
# str(e) or the apprise URL (may contain credentials), only type(e).__name__.
import logging
import socket

from atlas.business import expiry
from atlas.core import optional, security

logger = logging.getLogger(__name__)

# ponytail: known-safe apprise notification schemes — providers with their own
# fixed endpoint (mail relay, chat-bot API), not a generic webhook-to-anywhere.
# Operator can extend this set later if a new provider is needed.
ALLOWED_TARGET_SCHEMES = {
    # ONLY schemes with a FIXED provider host. 'ntfy' excluded: ntfy://<host>/topic
    # allows an arbitrary host (self-hosted) -> SSRF to loopback/LAN (Codex finding).
    "mailto", "mailtos", "tgram", "discord", "slack", "twilio", "pover", "pushover",
}


def _target_scheme_ok(target: str) -> bool:
    scheme = target.split("://", 1)[0].strip().lower() if "://" in target else ""
    return scheme in ALLOWED_TARGET_SCHEMES


def _mail_host_ok(target: str) -> bool:
    """For mailto/mailtos: the effective SMTP host must not be internal/loopback.
    apprise allows 'mailto://user:pass@host?smtp=<other-host>' — without this an
    authenticated user could redirect the send to 169.254/127.0.0.1/LAN (SSRF;
    Codex finding). Other schemes have a fixed provider host so don't need this."""
    import urllib.parse

    from atlas.core.net import _is_blocked_addr

    scheme = target.split("://", 1)[0].strip().lower()
    if scheme not in ("mailto", "mailtos"):
        return True
    p = urllib.parse.urlparse(target)
    # apprise 'smtp=' overrides the host. Parser differential (Codex): the key is
    # case-insensitive and apprise takes the LAST one — so we gather ALL 'smtp' values
    # (any case) and REJECT on a duplicate or if ANY (incl. netloc) resolves to an
    # internal host. ';' is treated as a separator alongside '&'.
    smtp_vals = []
    for k, vals in urllib.parse.parse_qs(p.query, separator="&").items():
        if k.strip().lower() == "smtp":
            smtp_vals.extend(vals)
    for k, vals in urllib.parse.parse_qs(p.query, separator=";").items():
        if k.strip().lower() == "smtp":
            smtp_vals.extend(vals)
    smtp_vals = [s.strip() for s in smtp_vals if s.strip()]
    if len(set(smtp_vals)) > 1:
        return False  # ambiguous smtp= -> don't risk it (apprise last-wins)
    hosts = [h for h in (smtp_vals or []) + [p.hostname or ""] if h]
    if not hosts:
        return False  # no destination -> don't send
    for host in hosts:
        try:
            addrs = socket.getaddrinfo(host, None)
        except OSError:
            return False
        if any(_is_blocked_addr(sa[4][0]) for sa in addrs):
            return False
    return True


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
    # target contains a password (mailto://user:pass@host) — stored encrypted in the
    # DB; decrypt only here for use. secretbox tolerates old plaintext records (fallback).
    target = secretbox.decrypt(row["messaging_target"], cfg) if row else ""

    if row is None or not row["messaging_consent"] or not target:
        status = "skipped_no_consent"
        _log(spine, client_id, channel, status, subject, body)
        return {"status": status, "client_id": client_id}

    if not _target_scheme_ok(target):
        # SSRF guard: an arbitrary scheme (http/json) would force apprise to reach
        # any host (bypassing egress). The scheme check is cheap, no network.
        status = "skipped_bad_target"
        logger.warning("messaging target rejected: disallowed scheme (client %s)", client_id)
        _log(spine, client_id, channel, status, subject, body)
        return {"status": status, "client_id": client_id}

    if dry_run:
        status = "dry_run"
        _log(spine, client_id, channel, status, subject, body)
        return {"status": status, "client_id": client_id}

    if not _mail_host_ok(target):  # only right before a real send (resolves host -> network)
        # mailto with smtp=internal-host / loopback = SSRF (Codex fold)
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

    raise ValueError(f"unknown filter: {filt!r}")


def send_to_filter(spine, cfg, filt: str, subject: str, body: str, dry_run: bool = True, **kw) -> dict:
    audience = build_audience(spine, filt, **kw)
    counts: dict[str, int] = {}
    for client_id in audience:
        result = send_to_client(spine, cfg, client_id, subject, body, dry_run=dry_run)
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {"audience": len(audience), "results": counts}
