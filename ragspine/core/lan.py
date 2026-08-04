"""LAN-only mrežni sloj za uredske uređaje (skeneri/pisači).

Zrcalo web egressa: safe_fetch smije SAMO van (blokira privatne adrese),
lan_fetch smije SAMO unutra — svaka resolvana adresa mora biti privatna/
loopback/link-local, bez redirecta, s limitom veličine. Čisti stdlib:
mDNS/DNS-SD discovery, eSCL (AirScan) sken, minimalni IPP Print-Job."""

import http.client
import re
import socket
import ssl
import struct
import time
import urllib.parse


class LanBlocked(Exception):
    pass


_AWS_IPV6_IMDS = None  # lijeno inicijaliziran (import unutar funkcije)


def _is_lan_addr(addr: str) -> bool:
    import ipaddress
    global _AWS_IPV6_IMDS
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    # IPv4-mapped IPv6 (::ffff:169.254.169.254) normaliziraj na ugniježđeni IPv4
    # pa ista pravila vrijede — inače bi metadata adresa prošla kroz IPv6 granu.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # NIKAD: unspecified (0.0.0.0/::), link-local (169.254/fe80 → cloud metadata),
    # multicast. Loopback je OK (device-add je admin-only, treba za testove).
    if ip.is_unspecified or ip.is_link_local or ip.is_multicast:
        return False
    if _AWS_IPV6_IMDS is None:
        _AWS_IPV6_IMDS = ipaddress.IPv6Address("fd00:ec2::254")
    if ip == _AWS_IPV6_IMDS:  # AWS IPv6 IMDS je ULA (is_private) — eksplicitno odbij
        return False
    if ip.is_loopback:
        return True
    return ip.is_private and not ip.is_reserved


def assert_lan_host(host: str, port: int) -> str:
    """Sve resolvane adrese moraju biti LAN — javna adresa = LanBlocked.
    Vraća prvu provjerenu adresu: pozivatelj se spaja NA NJU (ne re-resolva),
    čime je DNS-rebinding između provjere i spajanja mrtav."""
    try:
        addrs = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise LanBlocked(f"resolve failed: {e}") from e
    if not addrs:
        raise LanBlocked(f"resolve prazan: {host}")
    for _f, _t, _p, _c, sockaddr in addrs:
        if not _is_lan_addr(sockaddr[0]):
            raise LanBlocked(f"nije LAN adresa: {sockaddr[0]} ({host})")
    return addrs[0][4][0]


def lan_fetch(url: str, method: str = "GET", body: bytes | None = None,
              headers: dict | None = None, timeout: int = 60,
              max_bytes: int = 100_000_000) -> tuple[int, dict, bytes]:
    """HTTP prema uređaju NA LAN-u. Vraća (status, headers, body) — ne baca na
    4xx (eSCL poll koristi 404 kao 'kraj'). Ne prati redirecte."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise LanBlocked(f"scheme not allowed: {p.scheme}")
    host = p.hostname
    if not host:
        raise LanBlocked("no hostname")
    port = p.port or (443 if p.scheme == "https" else 80)
    # spajamo se na PROVJERENU adresu, Host header nosi originalno ime —
    # HTTPConnection(host) bi ponovno resolvao i otvorio rebinding prozor
    ip = assert_lan_host(host, port)
    if p.scheme == "https":
        # LAN uređaji dolaze sa self-signed certifikatima — verifikacija javnim
        # CA-ovima ovdje nema smisla; identitet čuva LAN-only adresa + registar.
        conn = http.client.HTTPSConnection(ip, port, timeout=timeout,
                                           context=ssl._create_unverified_context())
    else:
        conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    path = p.path or "/"
    if p.query:
        path += "?" + p.query
    hdrs = {"Host": p.netloc, **(headers or {})}
    try:
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise LanBlocked(f"odgovor veći od {max_bytes} B")
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


# --- mDNS / DNS-SD discovery --------------------------------------------------

_MDNS_GRP = "224.0.0.251"
_MDNS_PORT = 5353
SCANNER_SERVICES = ("_uscan._tcp.local", "_uscans._tcp.local")
PRINTER_SERVICES = ("_ipp._tcp.local",)


def _q(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        raw = label.encode()
        out += bytes([len(raw)]) + raw
    return out + b"\x00" + struct.pack(">HH", 12, 1)  # PTR, IN


def _query_packet(services) -> bytes:
    head = struct.pack(">HHHHHH", 0, 0, len(services), 0, 0, 0)
    return head + b"".join(_q(s) for s in services)


def _read_name(data: bytes, off: int, depth: int = 0) -> tuple[str, int]:
    if depth > 16:
        return "", off + 1  # pointer petlja — odustani
    labels = []
    while off < len(data):
        ln = data[off]
        if ln == 0:
            return ".".join(labels), off + 1
        if ln & 0xC0 == 0xC0:  # kompresijski pointer
            ptr = struct.unpack(">H", data[off:off + 2])[0] & 0x3FFF
            tail, _ = _read_name(data, ptr, depth + 1)
            labels.append(tail)
            return ".".join(labels), off + 2
        off += 1
        labels.append(data[off:off + ln].decode("utf-8", "replace"))
        off += ln
    return ".".join(labels), off


def _parse_records(data: bytes) -> list[dict]:
    try:
        _id, _fl, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
        off = 12
        for _ in range(qd):  # preskoči pitanja
            _n, off = _read_name(data, off)
            off += 4
        out = []
        for _ in range(an + ns + ar):
            name, off = _read_name(data, off)
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
            off += 10
            rdata = data[off:off + rdlen]
            rec = {"name": name, "type": rtype}
            if rtype == 12:  # PTR
                rec["ptr"], _ = _read_name(data, off)
            elif rtype == 33:  # SRV
                _pr, _w, port = struct.unpack(">HHH", rdata[:6])
                rec["port"] = port
                rec["target"], _ = _read_name(data, off + 6)
            elif rtype == 1 and rdlen == 4:  # A (kriva duljina = zlonamjeran paket)
                rec["addr"] = socket.inet_ntoa(rdata)
            elif rtype == 16:  # TXT
                txt, i = {}, 0
                while i < len(rdata):
                    ln = rdata[i]; i += 1
                    kv = rdata[i:i + ln].decode("utf-8", "replace"); i += ln
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        txt[k.lower()] = v
                rec["txt"] = txt
            off += rdlen
            out.append(rec)
        return out
    except (struct.error, IndexError, OSError):
        return []  # jedan pokvaren paket ne smije srušiti cijeli sweep


def _assemble(records: list[dict]) -> list[dict]:
    """PTR instance -> SRV (target, port) + A (addr) + TXT -> uređaji s URL-om."""
    srv = {r["name"]: r for r in records if r["type"] == 33}
    addr = {r["name"]: r["addr"] for r in records if r["type"] == 1}
    txt = {r["name"]: r.get("txt", {}) for r in records if r["type"] == 16}
    out = []
    for r in records:
        if r["type"] != 12:
            continue
        service = r["name"].lower()
        inst = r.get("ptr", "")
        s = srv.get(inst)
        if not s:
            continue
        ip = addr.get(s.get("target", ""))
        if not ip or not _is_lan_addr(ip):
            continue
        t = txt.get(inst, {})
        pretty = inst.split("._")[0].replace("\\032", " ")
        if service.startswith(("_uscan.", "_uscans.")):
            scheme = "https" if service.startswith("_uscans.") else "http"
            rs = (t.get("rs") or "eSCL").strip("/")
            out.append({"kind": "scanner", "name": pretty,
                        "url": f"{scheme}://{ip}:{s['port']}/{rs}"})
        elif service.startswith("_ipp."):
            rp = (t.get("rp") or "ipp/print").strip("/")
            out.append({"kind": "printer", "name": pretty,
                        "url": f"http://{ip}:{s['port']}/{rp}"})
    # dedup po URL-u
    seen, uniq = set(), []
    for d in out:
        if d["url"] not in seen:
            seen.add(d["url"]); uniq.append(d)
    return uniq


def discover(timeout: float = 2.5) -> list[dict]:
    """mDNS sweep za skenere (eSCL) i pisače (IPP). Best-effort: greška ili
    tiha mreža -> []. Ne dira ništa izvan multicast grupe."""
    services = (*SCANNER_SERVICES, *PRINTER_SERVICES)
    records: list[dict] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.4)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        try:
            sock.sendto(_query_packet(services), (_MDNS_GRP, _MDNS_PORT))
            end = time.monotonic() + timeout
            while time.monotonic() < end and len(records) < 2000:  # anti-flood cap
                try:
                    data, _src = sock.recvfrom(9000)
                    records.extend(_parse_records(data))
                except socket.timeout:
                    continue
        finally:
            sock.close()
    except OSError:
        return []
    return _assemble(records)


# --- eSCL (AirScan) sken ------------------------------------------------------

_SCAN_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.6</pwg:Version>
  <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
</scan:ScanSettings>"""


_MAX_SCAN_BYTES = 200_000_000
_MAX_SCAN_DOCS = 500


def escl_scan(base_url: str, timeout: int = 120) -> list[tuple[bytes, str]]:
    """Pokreni sken i pokupi SVE dokumente: [(bytes, content_type)].
    404 na NextDocument = kraj (eSCL spec). Diže LanBlocked/RuntimeError."""
    base = base_url.rstrip("/")
    bp = urllib.parse.urlsplit(base)
    status, headers, body = lan_fetch(
        f"{base}/ScanJobs", method="POST", body=_SCAN_SETTINGS.encode(),
        headers={"Content-Type": "text/xml"}, timeout=timeout)
    if status not in (200, 201) or not headers.get("location"):
        raise RuntimeError(f"skener odbio posao (HTTP {status})")
    job = urllib.parse.urljoin(base + "/", headers["location"]).rstrip("/")
    jp = urllib.parse.urlsplit(job)
    # Location mora ostati na ISTOM uređaju — skener ne smije preusmjeriti
    # GET/DELETE na drugi interni servis (aplikacijski redirect = SSRF)
    if (jp.scheme, jp.hostname, jp.port or bp.port) != (bp.scheme, bp.hostname, bp.port):
        raise RuntimeError(f"skener preusmjerio posao na drugi origin: {job!r}")
    docs: list[tuple[bytes, str]] = []
    total = 0
    finished = False
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, h, data = lan_fetch(f"{job}/NextDocument", timeout=timeout)
            if status == 404:
                finished = True  # nema više dokumenata — posao gotov
                break
            if status == 503:
                time.sleep(1.0)  # stranica još nije spremna
                continue
            if status != 200:
                raise RuntimeError(f"sken prekinut (HTTP {status})")
            total += len(data)
            if total > _MAX_SCAN_BYTES or len(docs) >= _MAX_SCAN_DOCS:
                raise RuntimeError("sken prevelik (limit stranica/veličine)")
            docs.append((data, h.get("content-type", "application/pdf")))
    finally:
        try:
            lan_fetch(job, method="DELETE", timeout=10)  # best-effort čišćenje
        except Exception:
            pass  # cleanup ne smije pojesti rezultat ni primarnu grešku
    if not finished:
        raise RuntimeError("sken nije dovršen u roku — djelomičan rezultat odbačen")
    if not docs:
        raise RuntimeError("skener nije vratio nijedan dokument")
    return docs


# --- IPP Print-Job (minimalni klijent) ----------------------------------------

def _ipp_attr(tag: int, name: str, value: bytes) -> bytes:
    n = name.encode()
    return bytes([tag]) + struct.pack(">H", len(n)) + n + struct.pack(">H", len(value)) + value


def ipp_print(url: str, document: bytes, doc_format: str = "application/pdf",
              job_name: str = "RAGSPINE") -> None:
    """Pošalji dokument na IPP pisač (Print-Job, IPP/1.1). Diže RuntimeError
    na ne-uspješan IPP status."""
    # RFC 7472: http -> ipp, https -> ipps
    ipp_uri = re.sub(r"^http://", "ipp://", re.sub(r"^https://", "ipps://", url))
    req = (b"\x01\x01"            # IPP/1.1
           + b"\x00\x02"          # Print-Job
           + b"\x00\x00\x00\x01"  # request-id
           + b"\x01"              # operation-attributes-tag
           + _ipp_attr(0x47, "attributes-charset", b"utf-8")
           + _ipp_attr(0x48, "attributes-natural-language", b"en")
           + _ipp_attr(0x45, "printer-uri", ipp_uri.encode())
           + _ipp_attr(0x42, "job-name", job_name.encode())
           + _ipp_attr(0x49, "document-format", doc_format.encode())
           + b"\x03"              # end-of-attributes
           + document)
    status, _h, body = lan_fetch(url, method="POST", body=req,
                                 headers={"Content-Type": "application/ipp"}, timeout=120)
    if status != 200 or len(body) < 4:
        raise RuntimeError(f"pisač odbio (HTTP {status})")
    ipp_status = struct.unpack(">H", body[2:4])[0]
    if ipp_status > 0x00FF:  # 0x0000-0x00ff = successful
        raise RuntimeError(f"IPP greška 0x{ipp_status:04x}")
