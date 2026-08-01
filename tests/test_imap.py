import os
import re
from email.message import EmailMessage

from ragspine.docs import imap_fetch


class FakeIMAP:
    """Minimal imaplib.IMAP4_SSL stand-in: uid('SEARCH'|'FETCH', ...)."""

    def __init__(self, messages: dict[int, bytes]):
        self.messages = messages

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            m = re.search(r"UID (\d+):\*", args[-1])
            start = int(m.group(1)) if m else 1
            uids = sorted(u for u in self.messages if u >= start)
            return ("OK", [" ".join(str(u) for u in uids).encode()])
        if cmd == "FETCH":
            uid = int(args[0])
            raw = self.messages[uid]
            return ("OK", [(b"1 (RFC822 {%d}" % len(raw), raw)])
        raise ValueError(cmd)


def _email_bytes(filename="racun.xml"):
    msg = EmailMessage()
    msg["Subject"] = "Racun"
    msg["From"] = "dobavljac@example.com"
    msg["To"] = "mi@example.com"
    msg.set_content("e-racun u prilogu")
    msg.add_attachment(
        b"<Invoice></Invoice>", maintype="application", subtype="xml", filename=filename
    )
    return msg.as_bytes()


def test_fetch_new_saves_attachments_and_advances_watermark(spine, cfg):
    imap = FakeIMAP({1: _email_bytes("racun1.xml"), 2: _email_bytes("racun2.xml")})

    result = imap_fetch.fetch_new(spine, cfg, imap=imap)

    assert result["fetched"] == 2
    assert len(result["attachments"]) == 2
    inbox = os.path.join(cfg.data_dir, "inbox")
    for path in result["attachments"]:
        assert os.path.exists(path)
        assert os.path.dirname(path) == inbox

    row = spine.read().execute(
        "SELECT last_uid FROM imap_state WHERE mailbox='INBOX'"
    ).fetchone()
    assert row["last_uid"] == 2

    result2 = imap_fetch.fetch_new(spine, cfg, imap=imap)
    assert result2["fetched"] == 0
    assert result2["attachments"] == []


def test_fetch_new_sanitizes_traversal_filename(spine, cfg):
    imap = FakeIMAP({1: _email_bytes("../../evil.xml")})

    result = imap_fetch.fetch_new(spine, cfg, imap=imap)

    inbox = os.path.join(cfg.data_dir, "inbox")
    expected = os.path.join(inbox, "evil.xml")
    assert result["attachments"] == [expected]
    assert os.path.exists(expected)
    assert os.path.realpath(expected) == os.path.realpath(expected)
    assert os.path.commonpath([os.path.realpath(expected), os.path.realpath(inbox)]) == os.path.realpath(inbox)
    assert not os.path.exists(os.path.realpath(os.path.join(inbox, "..", "..", "evil.xml")))
