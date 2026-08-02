from fastapi.testclient import TestClient

from ragspine.business import obveze
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _seed(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Zebra', '1', 'u sustavu pdv')")
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Alfa', '2', 'u sustavu pdv')")
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Beta', '3', 'nije u pdvu')")


def test_only_pdv_clients(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    assert [r["client"] for r in rows] == ["Alfa", "Zebra"]  # Beta van, abecedno


def test_mark_sent_moves_down(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    obveze.mark_sent(spine, rows[0]["obligation_id"], "ana")
    rows2 = obveze.list_period(spine, "PDV", "2026-07")
    assert rows2[-1]["client"] == "Alfa" and rows2[-1]["sent"]


def test_ensure_period_idempotent(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    assert len(rows) == 2


def test_mark_sent_unknown_obligation_raises(spine):
    import pytest
    with pytest.raises(ValueError):
        obveze.mark_sent(spine, 999, "ana")


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_api_obveze_html_escapes_client_name(spine, cfg):
    with spine.write() as conn:
        conn.execute(
            "INSERT INTO clients(name, oib, pdv_status) VALUES ('<script>Zloća</script>', '9', 'u sustavu pdv')"
        )
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-07", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<script>Zloća</script>" not in r.text
    assert "&lt;script&gt;Zloća&lt;/script&gt;" in r.text


def test_login_sets_cookie(spine, cfg):
    c = _client(spine, cfg)
    add_user(spine, "ana", "tajna")
    r = c.post("/auth/login", json={"username": "ana", "password": "tajna"})
    assert r.status_code == 200
    assert "ragspine_token" in r.cookies


def test_obveze_via_cookie_only(spine, cfg):
    _seed(spine)
    c = _client(spine, cfg)
    add_user(spine, "ana", "tajna")
    c.post("/auth/login", json={"username": "ana", "password": "tajna"})
    assert "ragspine_token" in c.cookies  # persisted on the client's cookie jar
    r = c.get("/obveze?kind=PDV&period=2026-07")  # no Authorization header
    assert r.status_code == 200
    assert "Alfa" in r.text


def test_obveze_no_auth_redirects_to_login(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/obveze", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_obveze_invalid_kind_400(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=BOGUS", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400


def test_obveze_mark_unknown_obligation_404(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/obveze/mark", json={"obligation_id": 999},
                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


def test_obveze_page_has_type_tabs_and_two_sections(spine, cfg):
    _seed(spine)
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-08", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    text = r.text
    # type selector tabs (PDV + JOPPD), PDV active
    assert 'class="obveze-tab active"' in text and "PDV</a>" in text
    assert "JOPPD</a>" in text
    # two sections: unsent above, predano below; checkbox marks + drops down
    assert "Za predati" in text and "Predano" in text
    assert "onToggle(this)" in text
    assert 'id="list-unsent"' in text and 'id="list-sent"' in text
    # month navigation
    assert "Kolovoz 2026." in text
    assert "period=2026-07" in text and "period=2026-09" in text


def test_obveze_page_sent_client_starts_in_predano(spine, cfg):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-08")
    rows = obveze.list_period(spine, "PDV", "2026-08")
    obveze.mark_sent(spine, rows[0]["obligation_id"], "ana")  # Alfa -> predano

    c = _client(spine, cfg)
    tok = _token(c, spine)
    text = c.get("/obveze?kind=PDV&period=2026-08",
                 headers={"Authorization": f"Bearer {tok}"}).text
    # the sent row renders checked (lives in the Predano section)
    sent_block = text.split('id="list-sent"')[1]
    assert "Alfa" in sent_block and "checked" in sent_block


def test_obveze_empty_state_hidden_when_section_has_rows(spine, cfg):
    _seed(spine)  # Alfa + Zebra are PDV obligors -> unsent section non-empty
    c = _client(spine, cfg)
    tok = _token(c, spine)
    text = c.get("/obveze?kind=PDV&period=2026-08",
                 headers={"Authorization": f"Bearer {tok}"}).text
    # the "Za predati" empty-state paragraph must be hidden while rows exist
    empty_p = [ln for ln in text.splitlines() if 'id="empty-unsent"' in ln][0]
    assert "display:none" in empty_p
