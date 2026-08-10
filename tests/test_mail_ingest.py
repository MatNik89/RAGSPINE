"""IMAP ingest ulazne pošte -> dokumenti (stdlib, fake IMAP, bez mreže)."""
import json
from email.message import EmailMessage

import pytest

from atlas.business import connector_adapters, mail_ingest, secretbox

connector_adapters.register_builtin()


def _raw(frm: str, subject: str, body: str) -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["Subject"] = subject
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    """Minimalni IMAP: login/select/uid(SEARCH|FETCH)/logout."""
    def __init__(self, host):
        self.host = host
        self._msgs = {1: _raw("ana@klijent.hr", "Ponuda", "Tekst ponude."),
                      2: _raw("nepoznat@x.com", "Račun", "Tijelo računa.")}
    def login(self, u, p): return ("OK", [b""])
    def select(self, folder): return ("OK", [b"2"])
    def status(self, folder, what): return ("OK", [b'"INBOX" (UIDVALIDITY 42)'])
    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            rng = args[-1]  # "UID N:*"
            lo = int(rng.split()[1].split(":")[0])
            hits = " ".join(str(u) for u in self._msgs if u >= lo).encode()
            return ("OK", [hits])
        if cmd == "FETCH":
            uid = int(args[0])
            if uid not in self._msgs:
                return ("OK", [None])
            return ("OK", [(f"{uid} (RFC822)".encode(), self._msgs[uid])])
        return ("NO", [b""])
    def logout(self): return ("OK", [b""])


def _mk_connector(spine, cfg, host="imap.example.com", email="ured@x.hr"):
    conf = {"host": host, "email": email,
            "password": secretbox.encrypt("tajna", cfg), "folder": "INBOX"}
    with spine.write() as c:
        return c.execute(
            "INSERT INTO connectors(kind, name, config_json, status) "
            "VALUES('mail_imap','Ured',?, 'connected')", (json.dumps(conf),)).lastrowid


def test_fetch_ingests_new_and_links_client(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, email) VALUES('Klijent', 'ana@klijent.hr')")
    cid = _mk_connector(spine, cfg)
    out = mail_ingest.fetch_new(spine, cfg, cid, imap_factory=FakeIMAP)
    assert out == {"ingested": 2, "last_uid": 2}
    docs = spine.read().execute(
        "SELECT title, doc_type, client_id FROM documents ORDER BY id").fetchall()
    assert [d["doc_type"] for d in docs] == ["email", "email"]
    # pošiljatelj poznat -> vezan na klijenta; nepoznat -> client_id NULL
    by_title = {d["title"]: d["client_id"] for d in docs}
    kid = spine.read().execute("SELECT id FROM clients WHERE email='ana@klijent.hr'").fetchone()["id"]
    assert by_title["Ponuda"] == kid and by_title["Račun"] is None


def test_fetch_is_idempotent(spine, cfg):
    cid = _mk_connector(spine, cfg)
    mail_ingest.fetch_new(spine, cfg, cid, imap_factory=FakeIMAP)
    out2 = mail_ingest.fetch_new(spine, cfg, cid, imap_factory=FakeIMAP)  # ništa novo
    assert out2["ingested"] == 0
    n = spine.read().execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    assert n == 2  # bez duplikata


class FailFetchIMAP(FakeIMAP):
    """FETCH pukne na UID 2 -> watermark ne smije preskočiti (contiguous prefix)."""
    def uid(self, cmd, *args):
        if cmd == "FETCH" and int(args[0]) == 2:
            return ("NO", [b"fail"])
        return super().uid(cmd, *args)


def test_watermark_contiguous_on_fetch_failure(spine, cfg):
    cid = _mk_connector(spine, cfg)
    out = mail_ingest.fetch_new(spine, cfg, cid, imap_factory=FailFetchIMAP)
    assert out["last_uid"] == 1  # stao na 1, UID 2 nije trajno preskočen
    # idući prolaz (bez greške) pokupi UID 2
    out2 = mail_ingest.fetch_new(spine, cfg, cid, imap_factory=FakeIMAP)
    assert out2["ingested"] == 1 and out2["last_uid"] == 2


def test_spoofed_from_ambiguous_not_linked(spine, cfg):
    # dva klijenta s istim e-mailom -> lažljivi From ne veže jednoznačno -> NULL
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, email) VALUES('A','ana@klijent.hr')")
        c.execute("INSERT INTO clients(name, email) VALUES('B','ana@klijent.hr')")
    cid = _mk_connector(spine, cfg)
    mail_ingest.fetch_new(spine, cfg, cid, imap_factory=FakeIMAP)
    d = spine.read().execute(
        "SELECT client_id FROM documents WHERE title='Ponuda'").fetchone()
    assert d["client_id"] is None


def test_wrong_connector_kind_rejected(spine, cfg):
    with spine.write() as c:
        wid = c.execute("INSERT INTO connectors(kind, name, config_json, status) "
                        "VALUES('telegram_gateway','tg','{}','connected')").lastrowid
    with pytest.raises(ValueError):
        mail_ingest.fetch_new(spine, cfg, wid, imap_factory=FakeIMAP)


def test_guard_blocks_internal_host():
    with pytest.raises(ValueError):
        mail_ingest._guard_host("127.0.0.1")
    with pytest.raises(ValueError):
        mail_ingest._guard_host("localhost")


def test_imap_connector_type_registered():
    from atlas.business import connectors
    t = connectors.get_type("mail_imap")
    assert t is not None and t.category == "mail"
    assert {f.key for f in t.fields} >= {"host", "email", "password"}
