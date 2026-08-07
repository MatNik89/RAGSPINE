from fastapi.testclient import TestClient

from atlas.web.api import create_app
from atlas.web.static import serve_static


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
    r2 = c.get("/static/../atlas/config.py")
    assert r2.status_code == 404


def test_serve_static_traversal_guard_direct():
    # exercises the realpath+commonpath guard directly — an HTTP client
    # normalizes ".." out of the URL before it ever reaches the route, so
    # this proves the guard itself (not just client-side URL cleanup).
    assert serve_static("../../../../../../etc/passwd").status_code == 404
    assert serve_static("fonts/../../../atlas/config.py").status_code == 404
    assert serve_static("fonts/PlexSans-400.woff2").status_code == 200


def test_static_traversal_allowed_extension_blocked_via_http(spine, cfg):
    # HTTP-level companion to the direct-call test below. Note: httpx
    # normalizes ".." out of the URL client-side before it's ever sent, so
    # this hits FastAPI's own not-found (no /static route matches the
    # normalized /etc/hostname.woff2 path) rather than proving our guard —
    # the direct serve_static() calls below are what actually exercise the
    # commonpath guard on a raw, un-normalized ".."-containing path.
    c = _client(spine, cfg)
    r = c.get("/static/fonts/../../../../../../etc/hostname.woff2")
    assert r.status_code == 404


def test_static_traversal_with_allowed_extension_blocked():
    # Every traversal case above uses a DISALLOWED extension (.py, no ext)
    # so they 404 at the extension gate and never reach the
    # realpath+commonpath guard. This one uses an ALLOWED extension
    # (.woff2) AND points at a REAL file that exists outside the static
    # dir — if the containment guard were missing/buggy, this would
    # actually be served (200). It must still 404, proving the guard
    # itself (not the extension allowlist, not a missing-file 404).
    assert serve_static("../../../../../../../etc/hostname.woff2").status_code == 404


def test_serve_static_blocks_real_file_outside_scoped_dir(tmp_path, monkeypatch):
    import atlas.web.static as static_mod

    static_root = tmp_path / "static"
    (static_root / "fonts").mkdir(parents=True)
    (static_root / "fonts" / "PlexSans-400.woff2").write_bytes(b"legit-font-bytes")
    outside_file = tmp_path / "secret.woff2"
    outside_file.write_bytes(b"secret-bytes-outside-static-dir")
    monkeypatch.setattr(static_mod, "STATIC_DIR", static_root.resolve())

    # sanity: a legit in-scope file is still served through the patched dir
    ok = static_mod.serve_static("fonts/PlexSans-400.woff2")
    assert ok.status_code == 200

    # traversal path resolves to a file that REALLY exists, with an
    # ALLOWED extension, sitting one level outside the scoped static dir —
    # this can only 404 via the commonpath guard, not via "file not found"
    # or the extension allowlist.
    rel = "fonts/../../secret.woff2"
    assert (static_root / rel).resolve() == outside_file
    assert static_mod.serve_static(rel).status_code == 404
