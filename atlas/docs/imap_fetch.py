"""IMAP fetch of e-invoice attachments with a UID watermark (imap_state)."""
import email
import imaplib
import os

from atlas.docs import eracun

ATTACHMENT_EXTS = (".xml", ".pdf")


def _attachments(msg):
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        # ponytail: os.path.basename strips any directory component an
        # attacker-controlled filename might carry — never trust it raw.
        safe_name = os.path.basename(filename)
        if not safe_name or not safe_name.lower().endswith(ATTACHMENT_EXTS):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        yield safe_name, payload


def fetch_new(spine, cfg, imap=None, mailbox="INBOX") -> dict:
    owns_imap = imap is None
    if owns_imap:
        imap = imaplib.IMAP4_SSL(cfg.imap_host)
        imap.login(cfg.imap_user, cfg.imap_pass)
        imap.select(mailbox)

    inbox_dir = os.path.join(cfg.data_dir, "inbox")
    os.makedirs(inbox_dir, exist_ok=True)

    row = spine.read().execute(
        "SELECT last_uid FROM imap_state WHERE mailbox=?", (mailbox,)
    ).fetchone()
    last_uid = row["last_uid"] if row else 0

    fetched = 0
    attachments = []
    try:
        typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        uids = data[0].split() if typ == "OK" and data and data[0] else []

        for raw_uid in uids:
            typ, msg_data = imap.uid("FETCH", raw_uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            saved = []
            for filename, payload in _attachments(msg):
                dest = os.path.join(inbox_dir, filename)
                with open(dest, "wb") as f:
                    f.write(payload)
                saved.append(dest)

            fetched += 1
            attachments.extend(saved)

            # Watermark advances only after this message's attachments are
            # safely on disk — a crash mid-fetch must not skip a message.
            with spine.write() as c:
                c.execute(
                    """INSERT INTO imap_state(mailbox, last_uid) VALUES(?,?)
                       ON CONFLICT(mailbox) DO UPDATE SET last_uid=excluded.last_uid""",
                    (mailbox, int(raw_uid)),
                )

            for path in saved:
                if path.lower().endswith(".xml"):
                    try:
                        eracun.autosort(spine, cfg, path)
                    except Exception:
                        pass
    finally:
        if owns_imap:
            try:
                imap.logout()
            except Exception:
                pass

    spine.audit("sustav", "imap_fetch", mailbox, f"{fetched} poruka")
    return {"fetched": fetched, "attachments": attachments}
