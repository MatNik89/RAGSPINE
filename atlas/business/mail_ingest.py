"""Ingest ulazne e-pošte (IMAP) u dokumente da je AI može čitati/pretraživati.
stdlib (imaplib+email) — bez novih ovisnosti. Povlači SAMO nove poruke (UID >
last_uid, vezano uz UIDVALIDITY), dedup po UID-u, veže na klijenta po e-mailu
pošiljatelja ako je JEDNOZNAČNO (From je lažljiv — vidi _client_for_sender).
`imap_factory` injektabilan za testove (bez mreže).

REZIDUAL (dokumentirano, follow-up): (a) sadržaj maila je NEPOVJERLJIV ulaz — završi
u RAG-u i može doći do LLM-a kao tool-result (prompt-injection). Ublaženo: agent
NE izvršava write bez potvrde (propose->confirm) + composer označava izvor kao
podatak; puna izolacija (untrust/quarantine + delimitirani tool-result) je veći
zahvat. (b) DNS-rebind TOCTOU na _guard_host (razriješi jednom, IMAP4_SSL opet) —
ublaženo hostname-verifikacijom TLS-a; pinning IP-a je upgrade path."""
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
    # From je LAŽLJIV (nema autentikacije) — vežemo na klijenta samo kao POMOĆ, i to
    # scopeano na org i JEDNOZNAČNO (>1 poklapanja = ne veži, ide u opći inbox).
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
        import ssl
        _guard_host(host)
        # provjeri certifikat + hostname (default konteksta) — inače LAN/DNS MITM
        # krade lozinku sandučića (Codex HIGH: goli IMAP4_SSL nije autentificiran)
        imap = imaplib.IMAP4_SSL(host, ssl_context=ssl.create_default_context())
    else:
        imap = factory(host)
    imap.login(email_addr, password)
    return imap


def _deadletter(spine, connector_id: int, uid: int, reason: str) -> None:
    """Zapiši neuspjeli mail u obavijesti (dead-letter) — ne nestaje tiho. Dedupe
    po (kind, body) unutar 7 dana kao ostali _notify_once pozivi. Body sadrži SAMO
    konektor/uid/tip-greške (NE subject/sadržaj maila) -> nema injection/XSS.
    ponytail: notifications su org-agnostične u ATLAS-u (kao folder_connected i sve
    ostale vrste) — multi-tenant org-scoping je pre-postojeći, širi refaktor."""
    body = f"Mail (konektor {connector_id}, uid {uid}) nije obrađen: {reason}"
    with spine.write() as c:
        seen = c.execute("SELECT 1 FROM notifications WHERE kind='mail_deadletter' "
                         "AND body=? AND at >= datetime('now','-7 days')", (body,)).fetchone()
        if seen is None:
            c.execute("INSERT INTO notifications(kind, body) VALUES('mail_deadletter', ?)", (body,))


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

    imap = _connect(host, email_addr, conf.get("password", ""), factory=imap_factory)
    ingested, last_uid, watermark, mbox_key = 0, 0, 0, None
    try:
        imap.select(folder)
        # UIDVALIDITY: ako se sandučić ponovno stvori/migrira, UID-ovi se resetiraju.
        # Vežemo watermark uz UIDVALIDITY (dio ključa) -> reset = svjež ključ, ne
        # tiho ignoriranje nove pošte pod zastarjelim watermarkom (Codex MED).
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
        watermark = last_uid  # napreduje SAMO kroz neprekinuti niz uspjeha (Codex MED)

        typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        if typ != "OK":
            return {"ingested": 0, "last_uid": last_uid}
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last_uid)
        for uid in uids[:_MAX_BATCH]:
            try:
                typ, mdata = imap.uid("FETCH", str(uid), "(RFC822)")
                if typ != "OK" or not mdata or not mdata[0]:
                    _deadletter(spine, connector_id, uid, f"FETCH {typ}")
                    break  # ne preskači UID trajno — stani, idući prolaz pokuša opet
                msg = email.message_from_bytes(mdata[0][1])
                subject = _decode(msg.get("Subject", "")) or "(bez naslova)"
                sender = _sender_email(msg)
                msgid = _decode(msg.get("Message-ID", ""))
                body = (_plain_body(msg) or "")[:_MAX_BODY]
                # UID+Message-ID u tekst -> dvije poruke istog tijela ostaju
                # ZASEBNI dokumenti (ingest dedupira po sha teksta; Codex MED)
                text = (f"[imap:{connector_id}:{uidv}:{uid} {msgid}]\n"
                        f"Od: {sender}\nNaslov: {subject}\n\n{body}")
                ingest_text(spine, text, subject[:200], doc_type="email",
                            client_id=_client_for_sender(spine, sender, org_id),
                            source_url=f"imap:{connector_id}:{uidv}:{uid}", org_id=org_id)
                ingested += 1
                watermark = uid  # tek sad je uid uspješno obrađen (neprekinuti prefiks)
            except Exception as e:
                # dead-letter: neispravna poruka ne nestaje tiho -> owner je vidi u
                # obavijestima (postojeći notifications red, ne novi store)
                _deadletter(spine, connector_id, uid, type(e).__name__)
                break  # jedna neispravna poruka NE truje cijeli konektor; stani ovdje
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
