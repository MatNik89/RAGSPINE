"""Ingest ulazne e-pošte (IMAP) u dokumente da je AI može čitati/pretraživati.
stdlib (imaplib+email) — bez novih ovisnosti. Povlači SAMO nove poruke (UID >
last_uid iz imap_state), dedup po UID-u, veže na klijenta po e-mailu pošiljatelja
ako se poklopi. `imap_factory` injektabilan za testove (bez mreže)."""
import email
import imaplib
from email.header import decode_header, make_header

from atlas.core.net import _is_blocked_addr

_MAX_BODY = 100_000  # rez tijela (anti-DoS na golemim mailovima)
_MAX_BATCH = 50      # koliko novih poruka po prolazu


def _decode(s) -> str:
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def _plain_body(msg) -> str:
    """Izvuci text/plain (fallback na bilo koji tekst). HTML se preskače —
    ingest indeksira čisti tekst, ne markup."""
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
    if not sender:
        return None
    row = spine.read().execute(
        "SELECT id FROM clients WHERE lower(email)=?", (sender,)).fetchone()
    return row["id"] if row else None


def _guard_host(host: str) -> None:
    """Mail server je vanjski; loopback/interni host = vjerojatno SSRF -> odbij.
    Razrješava ime (getaddrinfo prima i IP i hostname)."""
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
    if factory is None:  # samo za stvarne mrežne spojeve (test injektira factory)
        _guard_host(host)
    imap = (factory or imaplib.IMAP4_SSL)(host)
    imap.login(email_addr, password)
    return imap


def fetch_new(spine, cfg, connector_id: int, org_id=None, imap_factory=None) -> dict:
    """Povuci nove mailove za konfigurirani IMAP konektor i ingestaj ih u dokumente.
    Vrati {ingested, last_uid}. Idempotentno: dvaput zaredom ne duplira (UID watermark)."""
    from atlas.business import connectors
    from atlas.docs.ingest import ingest_text

    got = connectors.config_for_adapter(spine, connector_id, cfg, org_id=org_id)
    if got is None or got[0] != "mail_imap":
        raise ValueError("konektor nije IMAP e-pošta")
    conf = got[1]
    host = (conf.get("host") or "").strip()
    email_addr = (conf.get("email") or "").strip()
    folder = (conf.get("folder") or "INBOX").strip() or "INBOX"
    mbox_key = f"{connector_id}:{email_addr}:{folder}"

    row = spine.read().execute(
        "SELECT last_uid FROM imap_state WHERE mailbox=?", (mbox_key,)).fetchone()
    last_uid = row["last_uid"] if row else 0

    imap = _connect(host, email_addr, conf.get("password", ""), factory=imap_factory)
    ingested, max_uid = 0, last_uid
    try:
        imap.select(folder)
        typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        if typ != "OK":
            return {"ingested": 0, "last_uid": last_uid}
        uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid]
        for uid in sorted(uids)[:_MAX_BATCH]:
            typ, mdata = imap.uid("FETCH", str(uid), "(RFC822)")
            if typ != "OK" or not mdata or not mdata[0]:
                continue
            msg = email.message_from_bytes(mdata[0][1])
            subject = _decode(msg.get("Subject", "")) or "(bez naslova)"
            sender = _sender_email(msg)
            body = (_plain_body(msg) or "")[:_MAX_BODY]
            text = f"Od: {sender}\nNaslov: {subject}\n\n{body}"
            ingest_text(spine, text, subject[:200], doc_type="email",
                        client_id=_client_for_sender(spine, sender, org_id),
                        source_url=f"imap:{connector_id}:{uid}", org_id=org_id)
            ingested += 1
            max_uid = max(max_uid, uid)
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    if max_uid > last_uid:
        with spine.write() as c:
            c.execute("INSERT INTO imap_state(mailbox, last_uid) VALUES(?,?) "
                      "ON CONFLICT(mailbox) DO UPDATE SET last_uid=excluded.last_uid",
                      (mbox_key, max_uid))
    return {"ingested": ingested, "last_uid": max_uid}
