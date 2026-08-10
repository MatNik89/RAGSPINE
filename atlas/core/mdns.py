"""mDNS objava ATLAS servera (`_atlas._tcp`) + otkrivanje s klijenta. Server
odgovara na upite, radna stanica ga nađe bez ručnog upisivanja adrese.
Reuse: parsiranje kroz atlas.core.lan (_parse_records)."""
import socket
import struct
import time

from atlas.core import lan

SERVICE = "_atlas._tcp.local"
_MCAST = ("224.0.0.251", 5353)


def _name(n: str) -> bytes:
    out = b""
    for label in n.strip(".").split("."):
        raw = label.encode()
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _rr(name: str, rtype: int, rdata: bytes, ttl: int = 120) -> bytes:
    return _name(name) + struct.pack(">HHIH", rtype, 1, ttl, len(rdata)) + rdata


def atlas_response(ip: str, port: int, version: str = "", instance: str = "ATLAS") -> bytes:
    """Autoritativni mDNS odgovor: PTR + SRV + A + TXT za _atlas._tcp."""
    inst = f"{instance}.{SERVICE}"
    host = f"{instance.lower()}.local"
    ptr = _rr(SERVICE, 12, _name(inst))
    srv = _rr(inst, 33, struct.pack(">HHH", 0, 0, int(port)) + _name(host))
    a = _rr(host, 1, socket.inet_aton(ip))
    txt_kv = f"version={version}".encode() if version else b"atlas=1"
    txt = _rr(inst, 16, bytes([len(txt_kv)]) + txt_kv)
    # header: response (0x8400 = QR+AA), 0 pitanja, 4 odgovora
    head = struct.pack(">HHHHHH", 0, 0x8400, 0, 4, 0, 0)
    return head + ptr + srv + a + txt


def discover_atlas(timeout: float = 2.0, sock=None) -> list[dict]:
    """Pošalji upit za _atlas._tcp i vrati nađene servere [{host, port, name,
    version}]. `sock` injektabilan za testove.

    SAMO PRONALAŽENJE, NE POVJERENJE (Codex nalaz): mDNS zapisi su nepotpisani, pa
    bilo koji LAN host može lažirati odgovor. Rezultat je savjet čovjeku da nađe IP —
    agent se NIKAD ne smije automatski prijaviti na otkriveni host niti preuzeti
    sign_key s njega. Vjerodostojnost servera ide odvojeno: URL potvrđen pri
    instalaciji (opcija B) + owner koji otvori sparivanje i odobri uređaj."""
    close = False
    if sock is None:  # pragma: no cover — pravi multicast
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        sock.sendto(lan._query_packet((SERVICE,)), _MCAST)
        close = True
    found, seen = [], set()
    # ukupni deadline + cap: LAN napadač ne može flood-om beskonačno produžiti
    # sweep ni napuniti memoriju (Codex nalaz)
    deadline = time.monotonic() + max(timeout, 0.1) * 2
    try:
        while time.monotonic() < deadline and len(found) < 64:
            try:
                data, _addr = sock.recvfrom(9000)
            except (socket.timeout, OSError):
                break
            recs = lan._parse_records(data)
            srv = {r["name"]: r for r in recs if r["type"] == 33}
            addr = {r["name"]: r["addr"] for r in recs if r["type"] == 1}
            txt = {r["name"]: r.get("txt", {}) for r in recs if r["type"] == 16}
            for r in recs:
                if r["type"] != 12 or not r["name"].lower().startswith("_atlas."):
                    continue
                inst = r.get("ptr", "")
                s = srv.get(inst)
                if not s:
                    continue
                ip = addr.get(s.get("target", ""))
                if not ip or not lan._is_lan_addr(ip) or (ip, s["port"]) in seen:
                    continue
                seen.add((ip, s["port"]))
                found.append({"host": ip, "port": s["port"],
                              "name": inst.split("._")[0],
                              "version": txt.get(inst, {}).get("version", "")})
    finally:
        if close:
            sock.close()
    return found


def _parse_questions(data: bytes) -> list[tuple[str, int]]:
    """Vrati (ime, qtype) iz QUESTION sekcije (lan._parse_records preskače pitanja)."""
    try:
        _id, _fl, qd, *_ = struct.unpack(">HHHHHH", data[:12])
        off, out = 12, []
        for _ in range(qd):
            name, off = lan._read_name(data, off)
            qtype, _cls = struct.unpack(">HH", data[off:off + 4])
            off += 4
            out.append((name.lower(), qtype))
        return out
    except (struct.error, IndexError):
        return []


def _is_atlas_query(data: bytes) -> bool:
    """Istinit SAMO za pravi PTR upit za _atlas._tcp (ne za lažni answer-record —
    inače je responder reflection primitive; Codex nalaz)."""
    return any(name == SERVICE and qt == 12 for name, qt in _parse_questions(data))


def serve_responder(ip: str, port: int, version: str, stop) -> None:  # pragma: no cover — mreža
    """Slušaj mDNS upite i odgovori za _atlas._tcp. Pokreće se uz `atlas serve`."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 5353))
        mreq = struct.pack("4sl", socket.inet_aton(_MCAST[0]), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        resp = atlas_response(ip, port, version)
        while not stop.is_set():
            try:
                data, src = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if _is_atlas_query(data):  # odgovori SAMO na pravi PTR upit
                sock.sendto(resp, src)
    finally:
        sock.close()
