"""Ingest incoming e-mail (IMAP) into documents so the AI can read/search it.
stdlib (imaplib+email) — no new dependencies. Pulls ONLY new messages (UID >
last_uid, tied to UIDVALIDITY), dedup by UID, links to a client by the sender's
e-mail if it is UNAMBIGUOUS (From is spoofable — see _client_for_sender).
`imap_factory` is injectable for tests (no network).

RESIDUAL (documented, follow-up): (a) mail content is UNTRUSTED input — it ends
up in the RAG and can reach the LLM as a tool-result (prompt-injection). Mitigated:
the agent does NOT execute a write without confirmation (propose->confirm) + the
composer marks the source as data; full isolation (untrust/quarantine + delimited
tool-result) is a larger effort. (b) DNS-rebind TOCTOU on _guard_host (resolve once,
IMAP4_SSL again) — mitigated by TLS hostname verification; pinning the IP is the
upgrade path."""
import email
import imaplib
from email.header import decode_header, make_header

from atlas.core.net import _is_blocked_addr

_MAX_BODY = 100_000  # body cap (anti-DoS on huge mails)
_MAX_BATCH = 50      # how many new messages per pass


def _decode(s) -> str:
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def _plain_body(msg) -> str:
    """Extract text/plain (fallback to any text). HTML is skipped —
    ingest indexes plain text, not markup."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                    part.get("Content-Disposition") or ""):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", "replace")
    except Exception:
        return msg.get_payload() or ""


def _sender_email(msg) -> str:
    _name, addr = email.utils.parseaddr(msg.get("From", ""))
    return (addr or "").strip().lower()


def _client_for_sender(spine, sender: str, org_id=None):
    # From is SPOOFABLE (no authentication) — we link to a client only as a HINT, and
    # scoped to the org and UNAMBIGUOUS (>1 matches = do not link, goes to general inbox).
    if not sender:
        return None
    if org_id is None:
        rows = spine.read().execute(
            "SELECT id FROM clients WHERE lower(email)=?", (sender,)).fetchall()
    else:
        rows = spine.read().execute(
            "SELECT id FROM clients WHERE lower(email)=? AND (org_id=? OR org_id IS NULL)",
            (sender, org_id)).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def _guard_host(host: str) -> None:
    """The mail server is external; loopback/internal host = probably SSRF -> reject.
    Resolves the name (getaddrinfo accepts both IP and hostname)."""
    import socket
    if not host or host.strip().lower() == "localhost":
        raise ValueError("mail host je interni/loopback — odbijeno")
    try:
        addrs = socket.getaddrinfo(host, None)
    except OSError as e:
        raise ValueError(f"mail host se ne razrješava: {e}") from e
    if any(_is_blocked_addr(sa[4][0]) for sa in addrs):
        raise ValueError("mail host je interni/loopback — odbijeno")


def _connect(host: str, email_addr: str, password: str, factory=None):
    if factory is None:  # only for real network connections (tests inject a factory)
        import ssl
        _guard_host(host)
        # verify certificate + hostname (context default) — otherwise LAN/DNS MITM
        # steals the mailbox password (Codex HIGH: bare IMAP4_SSL is not authenticated)
        imap = imaplib.IMAP4_SSL(host, ssl_context=ssl.create_default_context())
    else:
        imap = factory(host)
    imap.login(email_addr, password)
    return imap


def _deadletter(spine, connector_id: int, uid: int, reason: str) -> None:
    """Record a failed mail in notifications (dead-letter) — it does not vanish
    silently. Dedupe by (kind, body) within 7 days like the other _notify_once calls.
    The body contains ONLY connector/uid/error-type (NOT the mail subject/content) ->
    no injection/XSS.
    ponytail: notifications are org-agnostic in ATLAS (like folder_connected and all
    other kinds) — multi-tenant org-scoping is pre-existing, a broader refactor."""
    body = f"Mail (konektor {connector_id}, uid {uid}) nije obrađen: {reason}"
    with spine.write() as c:
        seen = c.execute("SELECT 1 FROM notifications WHERE kind='mail_deadletter' "
                         "AND body=? AND at >= datetime('now','-7 days')", (body,)).fetchone()
        if seen is None:
            c.execute("INSERT INTO notifications(kind, body) VALUES('mail_deadletter', ?)", (body,))


def fetch_new(spine, cfg, connector_id: int, org_id=None, imap_factory=None) -> dict:
    """Pull new mails for the configured IMAP connector and ingest them into documents.
    Return {ingested, last_uid}. Idempotent: twice in a row does not duplicate (UID watermark)."""
    from atlas.business import connectors
    from atlas.docs.ingest import ingest_text

    got = connectors.config_for_adapter(spine, connector_id, cfg, org_id=org_id)
    if got is None or got[0] != "mail_imap":
        raise ValueError("konektor nije IMAP e-pošta")
    conf = got[1]
    host = (conf.get("host") or "").strip()
    email_addr = (conf.get("email") or "").strip()
    folder = (conf.get("folder") or "INBOX").strip() or "INBOX"

    imap = _connect(host, email_addr, conf.get("password", ""), factory=imap_factory)
    ingested, last_uid, watermark, mbox_key = 0, 0, 0, None
    try:
        imap.select(folder)
        # UIDVALIDITY: if the mailbox is recreated/migrated, UIDs are reset.
        # We tie the watermark to UIDVALIDITY (part of the key) -> reset = fresh key,
        # not silently ignoring new mail under a stale watermark (Codex MED).
        uidv = ""
        try:
            styp, sdata = imap.status(folder, "(UIDVALIDITY)")
            if styp == "OK" and sdata and sdata[0]:
                import re as _re
                m = _re.search(rb"UIDVALIDITY\s+(\d+)", sdata[0])
                uidv = m.group(1).decode() if m else ""
        except Exception:
            uidv = ""
        mbox_key = f"{connector_id}:{email_addr}:{folder}:{uidv}"

        row = spine.read().execute(
            "SELECT last_uid FROM imap_state WHERE mailbox=?", (mbox_key,)).fetchone()
        last_uid = row["last_uid"] if row else 0
        watermark = last_uid  # advances ONLY through an unbroken run of successes (Codex MED)

        typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        if typ != "OK":
            return {"ingested": 0, "last_uid": last_uid}
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last_uid)
        for uid in uids[:_MAX_BATCH]:
            try:
                typ, mdata = imap.uid("FETCH", str(uid), "(RFC822)")
                if typ != "OK" or not mdata or not mdata[0]:
                    _deadletter(spine, connector_id, uid, f"FETCH {typ}")
                    break  # do not skip the UID permanently — stop, next pass retries
                msg = email.message_from_bytes(mdata[0][1])
                subject = _decode(msg.get("Subject", "")) or "(bez naslova)"
                sender = _sender_email(msg)
                msgid = _decode(msg.get("Message-ID", ""))
                body = (_plain_body(msg) or "")[:_MAX_BODY]
                # UID+Message-ID into the text -> two messages with the same body stay
                # SEPARATE documents (ingest dedupes by text sha; Codex MED)
                text = (f"[imap:{connector_id}:{uidv}:{uid} {msgid}]\n"
                        f"Od: {sender}\nNaslov: {subject}\n\n{body}")
                ingest_text(spine, text, subject[:200], doc_type="email",
                            client_id=_client_for_sender(spine, sender, org_id),
                            source_url=f"imap:{connector_id}:{uidv}:{uid}", org_id=org_id)
                ingested += 1
                watermark = uid  # only now has this uid been processed successfully (unbroken prefix)
            except Exception as e:
                # dead-letter: a malformed message does not vanish silently -> the owner
                # sees it in notifications (existing notifications queue, not a new store)
                _deadletter(spine, connector_id, uid, type(e).__name__)
                break  # one malformed message does NOT poison the whole connector; stop here
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    if mbox_key is not None and watermark > last_uid:
        with spine.write() as c:
            c.execute("INSERT INTO imap_state(mailbox, last_uid) VALUES(?,?) "
                      "ON CONFLICT(mailbox) DO UPDATE SET last_uid=excluded.last_uid",
                      (mbox_key, watermark))
    return {"ingested": ingested, "last_uid": watermark}
