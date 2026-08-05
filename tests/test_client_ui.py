from datetime import date

from fastapi.testclient import TestClient

from ragspine.business import checklist, expiry as expiry_mod, obveze
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _seed_client(spine, name="Alfa", oib="11111111111", pausal_eur=200):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO clients(name, oib, pdv_status, active, pausal_eur) "
            "VALUES (?,?,'u sustavu pdv',1,?)",
            (name, oib, pausal_eur),
        )
        return cur.lastrowid


# ---------- /ui/klijenti ----------

def test_klijenti_page_authed(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/klijenti", headers=_auth(tok))
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Dodaj novog klijenta" in r.text
    assert "/clients" in r.text
    assert "@font-face" in r.text


def test_klijenti_page_no_auth_redirects(spine, cfg):
    add_user(spine, "_o", "pw")  # onboarding gotov → neautoriziran ide na /login, ne /ui/setup
    c = _client(spine, cfg)
    r = c.get("/ui/klijenti", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ---------- /ui/klijent/{id} ----------

def test_klijent_page_authed(spine, cfg):
    cid = _seed_client(spine)
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get(f"/ui/klijent/{cid}", headers=_auth(tok))
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert f"/clients/{cid}/karton.json" in r.text
    assert "Nadzorna ploča" in r.text  # shell nav present


def test_klijent_page_no_auth_redirects(spine, cfg):
    add_user(spine, "_o", "pw")
    c = _client(spine, cfg)
    r = c.get("/ui/klijent/1", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ---------- GET /clients/{id}/karton.json ----------

def test_karton_json_no_auth_401(spine, cfg):
    cid = _seed_client(spine)
    c = _client(spine, cfg)
    r = c.get(f"/clients/{cid}/karton.json")
    assert r.status_code == 401


def test_karton_json_unknown_client_404(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/clients/999/karton.json", headers=_auth(tok))
    assert r.status_code == 404


def test_karton_json_has_expected_keys_and_seeded_data(spine, cfg):
    # go through the real onboarding flow (not a raw INSERT) so nas_folder is
    # properly scoped — otherwise list_documents() would resolve to the
    # shared data_dir root and pick up unrelated files.
    from ragspine.business import onboarding
    cid = onboarding.create_client(
        spine, cfg, {"name": "Beta", "pdv_status": "u sustavu pdv", "pausal_eur": 180}, "ana"
    )["id"]
    from ragspine.business import notes as notes_mod
    notes_mod.add(spine, cid, "ana", "Prva bilješka")

    with spine.write() as c:
        c.execute(
            "INSERT INTO sop_pages(title, client_id, category, content, status, author) "
            "VALUES ('Kako radimo PDV', ?, 'pdv', 'sadrzaj', 'approved', 'ana')",
            (cid,),
        )

    period = date.today().strftime("%Y-%m")
    obveze.ensure_period(spine, "PDV", period)

    expiry_mod.add(spine, cid, "osobna", "Osobna iskaznica", "2030-01-01")

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get(f"/clients/{cid}/karton.json", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()

    assert set(body) == {"client", "checklist", "notes", "sops", "obligations",
                          "expiry", "cjenik", "eracuni", "documents"}

    assert body["client"]["name"] == "Beta"
    assert body["client"]["pausal_eur"] == 180

    assert isinstance(body["checklist"]["score"], int)
    assert isinstance(body["checklist"]["missing"], list)

    assert any(n["body"] == "Prva bilješka" for n in body["notes"])

    assert any(s["title"] == "Kako radimo PDV" for s in body["sops"])

    assert any(o["kind"] == "PDV" for o in body["obligations"])

    assert any(e["label"] == "Osobna iskaznica" for e in body["expiry"])

    assert "ukupno" in body["cjenik"]
    assert "usporedba" in body["cjenik"]

    assert body["eracuni"]["count"] == 0
    assert body["eracuni"]["recent"] == []

    assert body["documents"] == []


def test_karton_json_best_effort_survives_section_failure(spine, cfg, monkeypatch):
    cid = _seed_client(spine)

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(checklist, "score_client", _boom)

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get(f"/clients/{cid}/karton.json", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["checklist"] in ({"score": 0, "missing": []},
                                  {"score": 0, "missing": [], "client": "Alfa"})


def test_karton_json_xss_safe_client_name_and_note(spine, cfg):
    cid = _seed_client(spine, name="<script>alert(1)</script>", oib="22222222222")
    from ragspine.business import notes as notes_mod
    notes_mod.add(spine, cid, "ana", "<b>bold</b> note")

    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get(f"/clients/{cid}/karton.json", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["client"]["name"] == "<script>alert(1)</script>"
    assert any(n["body"] == "<b>bold</b> note" for n in body["notes"])
    assert "application/json" in r.headers["content-type"]


def test_karton_page_no_external_assets_and_textcontent(spine, cfg):
    cid = _seed_client(spine)
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get(f"/ui/klijent/{cid}", headers=_auth(tok))
    assert "http://" not in r.text
    assert "https://" not in r.text
    assert "innerHTML" not in r.text
    assert "textContent" in r.text


def test_klijenti_page_no_external_assets_and_textcontent(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/klijenti", headers=_auth(tok))
    assert "http://" not in r.text
    assert "https://" not in r.text
    assert "innerHTML" not in r.text
    assert "textContent" in r.text
