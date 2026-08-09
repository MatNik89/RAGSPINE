"""PWA: manifest + service worker + ikone + <head> uklapanje. Omota postojeći
web UI da se instalira kao aplikacija (bez prepisivanja ekrana)."""
import json

from fastapi.testclient import TestClient

from atlas.web.api import create_app
from atlas.web.templates_ui import page_shell


def _c(spine, cfg):
    return TestClient(create_app(spine, cfg))


def test_manifest_served_valid(spine, cfg):
    r = _c(spine, cfg).get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(r.content)
    assert m["name"] == "ATLAS" and m["start_url"] == "/" and m["scope"] == "/"
    assert m["display"] == "standalone"
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    assert ("192x192", "any") in sizes
    assert ("512x512", "any") in sizes
    assert ("512x512", "maskable") in sizes  # maskable za Android krug/oblik


def test_service_worker_served_root_scope(spine, cfg):
    r = _c(spine, cfg).get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    # SW na rootu -> scope '/'; mora imati install+fetch handler (installability)
    assert "addEventListener('install'" in r.text
    assert "addEventListener('fetch'" in r.text
    assert "no-cache" in r.headers.get("cache-control", "").lower()


def test_icons_served_as_png(spine, cfg):
    c = _c(spine, cfg)
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png",
                 "apple-touch-icon.png", "favicon-32.png"):
        r = c.get(f"/static/icons/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = c.get("/static/icons/favicon.svg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml"


def test_offline_fallback_served(spine, cfg):
    r = _c(spine, cfg).get("/offline.html")
    assert r.status_code == 200 and "ATLAS" in r.text


def test_head_wires_pwa():
    head = page_shell("Test", "<p>x</p>")
    assert '<link rel="manifest" href="/manifest.webmanifest">' in head
    assert '<meta name="theme-color"' in head
    assert '/static/icons/apple-touch-icon.png' in head
    assert '/static/icons/favicon.svg' in head
    # SW registracija (guardana na secure context kroz 'serviceWorker' in navigator)
    assert "serviceWorker" in head and "/sw.js" in head
