import http.server, threading
import pytest
from atlas.core.net import safe_fetch, EgressBlocked

@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost/x",
                                 "http://192.168.1.1/x", "ftp://porezna.hr/x", "file:///etc/passwd"])
def test_blocked(url, cfg):
    with pytest.raises(EgressBlocked): safe_fetch(url)

def test_allowlist(cfg, monkeypatch):
    cfg.egress_allow.append("127.0.0.1")
    # dalje puca na connection refused, NE na EgressBlocked
    with pytest.raises(OSError): safe_fetch("http://127.0.0.1:1/x", timeout=1)

def test_redirect_blocked(cfg):
    # allow-listed host that tries to redirect us to a loopback target must NOT
    # be silently followed — that would bypass the allowlist/IP check entirely.
    cfg.egress_allow.append("127.0.0.1")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/evil")
            self.end_headers()
        def log_message(self, *a): pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with pytest.raises(EgressBlocked):
            safe_fetch(f"http://127.0.0.1:{port}/x")
    finally:
        server.shutdown()
        t.join(timeout=2)


def test_resolve_pin_rejects_any_blocked_answer(monkeypatch):
    # ime s javnim I privatnim A-zapisom -> odbij (anti DNS-rebind mix)
    from atlas.core import net
    monkeypatch.setattr(net.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443)),
                                         (2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(EgressBlocked):
        net._resolve_pin("evil.example", 443, check=True)


def test_resolve_pin_returns_first_public(monkeypatch):
    from atlas.core import net
    monkeypatch.setattr(net.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443)),
                                         (2, 1, 6, "", ("1.1.1.1", 443))])
    assert net._resolve_pin("ok.example", 443, check=True) == "8.8.8.8"


def test_resolve_pin_literal_blocked():
    from atlas.core import net
    with pytest.raises(EgressBlocked):
        net._resolve_pin("169.254.169.254", 80, check=True)  # cloud-metadata
