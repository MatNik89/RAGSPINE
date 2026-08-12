import http.client, ipaddress, socket, ssl, urllib.parse


class EgressBlocked(Exception):
    pass


def _is_blocked_addr(addr: str) -> bool:
    # block ALL non-global ranges (incl. CGNAT 100.64/10, which is not "private"
    # but is not internet-routable) — is_global is the tightest correct check for egress
    ip = ipaddress.ip_address(addr)
    return (not ip.is_global) or ip.is_reserved


def _resolve_pin(host: str, port: int, check: bool) -> str:
    """Resolve the name and return the IP we WILL connect to. If `check`, reject when
    ANY answer falls into a blocked range (a name with both a public AND a private
    A-record does not pass). A literal IP is returned as-is. The returned IP is
    pinned into the connection (Host+SNI keep the name) -> no DNS-rebind TOCTOU
    (guard pattern from OpenWorker)."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if check and _is_blocked_addr(host):
            raise EgressBlocked(f"blocked address: {host}")
        return host
    try:
        addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise EgressBlocked(f"resolve failed: {e}") from e
    pin = None
    for _f, _t, _p, _c, sockaddr in addrs:
        ip = sockaddr[0]
        if check and _is_blocked_addr(ip):
            raise EgressBlocked(f"blocked address: {ip}")
        if pin is None:
            pin = ip
    if pin is None:
        raise EgressBlocked(f"no address for {host}")
    return pin


def safe_fetch(url: str, timeout: int = 20, max_bytes: int = 5_000_000, headers: dict | None = None) -> bytes:
    from atlas.config import get_config

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise EgressBlocked(f"scheme not allowed: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise EgressBlocked("no hostname")

    allow = host in get_config().egress_allow
    if not allow and host == "localhost":
        raise EgressBlocked("localhost blocked")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pin = _resolve_pin(host, port, check=not allow)  # connect to EXACTLY the vetted IP

    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=timeout,
                                           context=ssl.create_default_context())
        # connect to the pinned IP but keep the SNI+cert-check ON THE NAME
        raw = socket.create_connection((pin, port), timeout=timeout)
        conn.sock = conn._context.wrap_socket(raw, server_hostname=host)
    else:
        conn = http.client.HTTPConnection(pin, port, timeout=timeout)  # Host header = name (below)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        conn.request("GET", path,
                     headers={"Host": host, "User-Agent": "ATLAS/1.0", **(headers or {})})
        resp = conn.getresponse()
        # a redirect is NOT followed (a 3xx target would bypass the allowlist/IP check)
        if resp.status in (301, 302, 303, 307, 308):
            raise EgressBlocked(f"redirect blocked: {resp.getheader('Location')}")
        return resp.read(max_bytes)
    finally:
        conn.close()
