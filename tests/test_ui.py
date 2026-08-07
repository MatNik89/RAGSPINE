from fastapi.testclient import TestClient

from atlas.business import sop as sop_mod
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    complete_setup(spine)
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
    complete_setup(spine)
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
    complete_setup(spine)
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
    complete_setup(spine)
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
    assert "atlas-theme" in r.text


def test_chat_and_upute_functionality_preserved(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r_chat = c.get("/ui/chat", headers=_auth(tok))
    assert "/chat" in r_chat.text
    assert "same-origin" in r_chat.text
    r_upute = c.get("/ui/upute", headers=_auth(tok))
    assert "/sop" in r_upute.text
    assert "/sop/" in r_upute.text and "image" in r_upute.text


# --- ui-nalazi-2026-08-07.md, prvi krug (mehanički) ---------------------


def test_container_ne_centrira_se_kao_grid_item():
    """B1: .container je grid item u .layout — auto-margin ga shrink-wrapa i
    centrira umjesto da ispuni stupac. .container smije zadržati max-width,
    ali ne smije nositi margin:0 auto; .layout > .container mora ga razvući."""
    from atlas.web.templates_ui import CSS_TOKENS
    assert "margin:0 auto" not in CSS_TOKENS
    assert ".layout > .container{justify-self:stretch;width:100%;margin-inline:0}" in CSS_TOKENS


def test_preflight_bez_literalnih_escapeova():
    """B2: \\u010d/\\u2014 upisani kao literal u obični string ostaju doslovno
    u HTML-u (HTML ne poznaje \\u escape). U JS-u (unutar <script>) je ispravno
    i ostaje netaknuto — provjeravamo samo dio prije prvog <script>."""
    from atlas.web.templates_preflight import preflight_page
    html_out = preflight_page()
    head = html_out.split("<script>")[0]
    assert "\\u" not in head, "literalni \\u escape iscurio u HTML"
    assert "Računalo i modeli" in html_out
    assert "—" in html_out


def test_klijenti_ima_poveznicu_na_uvoz():
    """B8: /ui/klijenti-uvoz je bio dohvatljiv samo upisom URL-a ručno."""
    from atlas.web.templates_ui import klijenti_page
    assert 'href="/ui/klijenti-uvoz"' in klijenti_page()


def test_svi_active_kljucevi_postoje_u_nav():
    """B9: page_shell(active=...) uspoređuje s ključevima iz _NAV — svaki
    active="..." u bilo kojem templates_*.py mora postojati u _NAV, inače
    sidebar nikad ne označi trenutnu stranicu."""
    import re
    import pathlib

    from atlas.web.templates_ui import _NAV
    kljucevi = {k for k, _, _ in _NAV}
    web_dir = pathlib.Path(__file__).resolve().parent.parent / "atlas" / "web"
    for f in web_dir.glob("templates_*.py"):
        for m in re.finditer(r'active="([a-z\-]+)"', f.read_text(encoding="utf-8")):
            assert m.group(1) in kljucevi, f"{f.name}: nepoznat active={m.group(1)!r}"


def test_design_system_ekrani_bez_hardkodiranih_boja():
    """B5: setup/preflight/connectors/backup su zaobilazili CSS_TOKENS i
    hardkodirali Tailwindove default hex boje. Login čeka poseban redizajn
    i namjerno je izuzet."""
    import re
    import pathlib

    web_dir = pathlib.Path(__file__).resolve().parent.parent / "atlas" / "web"
    for name in ("templates_setup.py", "templates_preflight.py",
                 "templates_connectors.py", "templates_backup.py"):
        text = (web_dir / name).read_text(encoding="utf-8")
        hits = set(re.findall(r'#[0-9a-fA-F]{6}', text))
        # setup.py redeklarira paletu u svom :root (nema page_shell) —
        # to su iste vrijednosti kao CSS_TOKENS, ne nove boje.
        allowed = set() if name != "templates_setup.py" else {
            "#DBD5C7", "#C7BFAD", "#7A7266", "#FBFAF5",
            "#9B2C2C", "#2F7D4F", "#B45309",
        }
        assert hits <= allowed, f"{name}: hardkodirane boje {hits - allowed}"
