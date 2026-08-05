from fastapi.testclient import TestClient

from ragspine.business import sop as sop_mod
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _seed_pending_sop(spine, title="Kako se radi X"):
    sop_id = sop_mod.create_sop(spine, "ana", title, "opce", "sadrzaj")
    sop_mod.submit_draft(spine, sop_id, "ana")
    return sop_id


def test_home_page_authed_shows_nav(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/", headers=_auth(tok))
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "/ui/chat" in r.text
    assert "/ui/upute" in r.text
    assert "/obveze" in r.text
    assert "Nadzorna ploča" in r.text


def test_home_page_no_auth_redirects_to_login(spine, cfg):
    add_user(spine, "_o", "pw")
    c = _client(spine, cfg)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_chat_page_authed(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/chat", headers=_auth(tok))
    assert r.status_code == 200
    assert '<input' in r.text
    assert "/chat" in r.text
    assert "same-origin" in r.text


def test_chat_page_no_auth_redirects(spine, cfg):
    add_user(spine, "_o", "pw")
    c = _client(spine, cfg)
    r = c.get("/ui/chat", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_upute_page_authed_lists_pending_and_forms(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    _seed_pending_sop(spine, title="Obrada ulaznih racuna")
    r = c.get("/ui/upute", headers=_auth(tok))
    assert r.status_code == 200
    assert "/sop" in r.text
    assert "/sop/" in r.text and "image" in r.text
    assert "Obrada ulaznih racuna" in r.text


def test_upute_page_no_auth_redirects(spine, cfg):
    add_user(spine, "_o", "pw")
    c = _client(spine, cfg)
    r = c.get("/ui/upute", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_upute_page_escapes_xss_title(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    _seed_pending_sop(spine, title="<script>alert(1)</script>")
    r = c.get("/ui/upute", headers=_auth(tok))
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text


def test_pages_have_no_external_assets(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    _seed_pending_sop(spine)
    for url in ("/", "/ui/chat", "/ui/upute"):
        r = c.get(url, headers=_auth(tok))
        assert r.status_code == 200
        assert "http://" not in r.text
        assert "https://" not in r.text


def test_pages_use_design_system_shell(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    _seed_pending_sop(spine)
    for url in ("/", "/ui/chat", "/ui/upute"):
        r = c.get(url, headers=_auth(tok))
        assert r.status_code == 200
        text = r.text
        # self-hosted fonts, no CDN
        assert "@font-face" in text
        assert "/static/fonts/PlexSans-400.woff2" in text
        assert "/static/fonts/PlexMono-400.woff2" in text
        # nav present with the required links
        assert "Nadzorna ploča" in text
        assert "Chat" in text
        assert "Klijenti" in text
        assert "Upute" in text
        # theme-init runs before paint, no-flash
        assert "data-theme" in text
        assert "localStorage" in text
        # design tokens
        assert "--accent" in text
        assert "--bg" in text


def test_theme_toggle_present(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/", headers=_auth(tok))
    assert r.status_code == 200
    assert "toggleTheme" in r.text
    assert 'id="theme-toggle"' in r.text
    assert "ragspine-theme" in r.text


def test_chat_and_upute_functionality_preserved(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r_chat = c.get("/ui/chat", headers=_auth(tok))
    assert "/chat" in r_chat.text
    assert "same-origin" in r_chat.text
    r_upute = c.get("/ui/upute", headers=_auth(tok))
    assert "/sop" in r_upute.text
    assert "/sop/" in r_upute.text and "image" in r_upute.text
