import ipaddress, socket, urllib.parse, urllib.request


class EgressBlocked(Exception):
    pass


def _is_blocked_addr(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to auto-follow redirects — a redirect target bypasses our
    host/IP allowlist check entirely if followed silently."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EgressBlocked(f"redirect blocked: {newurl}")


_opener = urllib.request.build_opener(_NoRedirect)


def safe_fetch(url: str, timeout: int = 20, max_bytes: int = 5_000_000, headers: dict | None = None) -> bytes:
    from ragspine.config import get_config

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise EgressBlocked(f"scheme not allowed: {parsed.scheme}")

    host = parsed.hostname
    if not host:
        raise EgressBlocked("no hostname")

    if host not in get_config().egress_allow:
        if host == "localhost":
            raise EgressBlocked("localhost blocked")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addrs = socket.getaddrinfo(host, port)
        except socket.gaierror as e:
            raise EgressBlocked(f"resolve failed: {e}") from e
        # ponytail: resolve-then-fetch has a DNS-rebinding TOCTOU gap (IP checked here
        # can differ from the IP urlopen connects to moments later) — accepted for v1
        # single-trusted-deploy; upgrade path = resolve once, pin IP into the request.
        for family, _, _, _, sockaddr in addrs:
            if _is_blocked_addr(sockaddr[0]):
                raise EgressBlocked(f"blocked address: {sockaddr[0]}")

    req = urllib.request.Request(url, headers={"User-Agent": "RAGSPINE/1.0", **(headers or {})})
    with _opener.open(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
        return data[:max_bytes]
