from fastapi.testclient import TestClient

from ragspine.web.api import create_app
from ragspine.web.static import serve_static


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def test_static_font_served_no_auth(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/static/fonts/PlexSans-400.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"
    assert len(r.content) > 0
    assert "cache-control" in r.headers
    assert "max-age" in r.headers["cache-control"]


def test_static_mono_font_served(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/static/fonts/PlexMono-500.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"


def test_static_traversal_blocked(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/static/../../etc/passwd")
    assert r.status_code == 404
    r2 = c.get("/static/%2e%2e/%2e%2e/etc/passwd")
    assert r2.status_code == 404


def test_static_nonexistent_404(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/static/fonts/nonexistent.woff2")
    assert r.status_code == 404


def test_static_disallowed_extension_404(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/static/some.py")
    assert r.status_code == 404
    r2 = c.get("/static/../ragspine/config.py")
    assert r2.status_code == 404


def test_serve_static_traversal_guard_direct():
    # exercises the realpath+commonpath guard directly — an HTTP client
    # normalizes ".." out of the URL before it ever reaches the route, so
    # this proves the guard itself (not just client-side URL cleanup).
    assert serve_static("../../../../../../etc/passwd").status_code == 404
    assert serve_static("fonts/../../../ragspine/config.py").status_code == 404
    assert serve_static("fonts/PlexSans-400.woff2").status_code == 200
