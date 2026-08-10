"""mDNS objava/otkrivanje ATLAS servera (_atlas._tcp)."""
import socket

from atlas.core import lan, mdns


def test_atlas_response_parses_back():
    pkt = mdns.atlas_response("192.168.1.10", 8443, version="1.0.0")
    recs = mdns.lan._parse_records(pkt)
    types = {r["type"] for r in recs}
    assert {12, 33, 1, 16} <= types  # PTR + SRV + A + TXT
    srv = [r for r in recs if r["type"] == 33][0]
    a = [r for r in recs if r["type"] == 1][0]
    txt = [r for r in recs if r["type"] == 16][0]
    assert srv["port"] == 8443
    assert a["addr"] == "192.168.1.10"
    assert txt["txt"]["version"] == "1.0.0"


class _FakeSock:
    """Vrati jedan atlas_response paket pa timeout."""
    def __init__(self, packets):
        self._pk = list(packets)

    def recvfrom(self, n):
        if self._pk:
            return self._pk.pop(0), ("192.168.1.10", 5353)
        raise socket.timeout()


def test_discover_atlas_finds_server():
    pkt = mdns.atlas_response("192.168.1.10", 8443, version="1.0.0", instance="ATLAS")
    out = mdns.discover_atlas(sock=_FakeSock([pkt]))
    assert len(out) == 1
    assert out[0]["host"] == "192.168.1.10" and out[0]["port"] == 8443
    assert out[0]["version"] == "1.0.0"


def test_discover_ignores_non_lan():
    pkt = mdns.atlas_response("8.8.8.8", 8443)  # javna adresa -> odbaci
    assert mdns.discover_atlas(sock=_FakeSock([pkt])) == []


def test_discover_dedups():
    pkt = mdns.atlas_response("192.168.1.10", 8443)
    out = mdns.discover_atlas(sock=_FakeSock([pkt, pkt]))
    assert len(out) == 1  # isti server dvaput -> jednom


def test_discover_survives_garbage_packet():
    good = mdns.atlas_response("192.168.1.5", 8443)
    out = mdns.discover_atlas(sock=_FakeSock([b"\x00\x01garbage", good]))
    assert len(out) == 1 and out[0]["host"] == "192.168.1.5"  # smeće ne ruši sweep


def test_discover_caps_results():
    # Codex: LAN flood ne smije neograničeno puniti memoriju -> cap 64
    pkts = [mdns.atlas_response(f"192.168.{i//254}.{i%254+1}", 8443) for i in range(200)]
    out = mdns.discover_atlas(sock=_FakeSock(pkts))
    assert len(out) <= 64


def test_responder_answers_only_real_query():
    # Codex: pravi PTR upit -> True; lažni answer-record (reflection) -> False
    query = lan._query_packet((mdns.SERVICE,))
    assert mdns._is_atlas_query(query) is True
    fake_answer = mdns.atlas_response("192.168.1.10", 8443)  # answer, ne question
    assert mdns._is_atlas_query(fake_answer) is False
