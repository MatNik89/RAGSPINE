"""First-run wizard: gatekeeper + kreiranje operatera."""
from fastapi.testclient import TestClient

from ragspine.web.api import create_app
from ragspine.web.deps import add_user
from ragspine.web import firstrun


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg), follow_redirects=False)


def test_needs_onboarding_true_when_no_users(spine):
    assert firstrun.needs_onboarding(spine) is True
    add_user(spine, "ana", "pw")
    assert firstrun.needs_onboarding(spine) is False


def test_gatekeeper_redirects_nav_to_setup(spine, cfg):
    c = _client(spine, cfg)
    # navigacija prije ijednog korisnika → 303 na /ui/setup
    r = c.get("/obveze")
    assert r.status_code == 303 and r.headers["location"] == "/ui/setup"
    r2 = c.get("/ui/klijenti")
    assert r2.status_code == 303 and r2.headers["location"] == "/ui/setup"
    # sam wizard + preflight + login se NE preusmjeravaju
    assert c.get("/ui/setup").status_code == 200
    assert c.get("/preflight").status_code == 200      # onboarding-allow
    assert c.get("/login").status_code == 200


def test_setup_owner_creates_first_user_then_locks(spine, cfg):
    c = _client(spine, cfg)
    r = c.post("/setup/owner", json={"username": "ana", "password": "TajnaLoz1!"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert firstrun.needs_onboarding(spine) is False
    # drugi poziv odbijen (postavljanje gotovo)
    r2 = c.post("/setup/owner", json={"username": "boris", "password": "DrugaLoz2!"})
    assert r2.status_code == 409
    # nakon kreiranja, gatekeeper više ne preusmjerava; a preflight traži prijavu
    assert c.get("/obveze").status_code in (200, 303)  # 303 na /login (require), ne /ui/setup
    loc = c.get("/obveze").headers.get("location", "")
    assert "/ui/setup" not in loc


def test_setup_owner_validates(spine, cfg):
    c = _client(spine, cfg)
    # prazno ime + prekratka lozinka → pydantic 422
    assert c.post("/setup/owner", json={"username": "", "password": "x"}).status_code == 422


def test_first_login_makes_owner(spine, cfg):
    c = _client(spine, cfg)
    c.post("/setup/owner", json={"username": "ana", "password": "TajnaLoz1!"})
    tok = c.post("/auth/login", json={"username": "ana", "password": "TajnaLoz1!"}).json()["token"]
    # owner vidi admin-only preflight
    assert c.get("/preflight", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_redirect_target_tight_matching():
    # goli startswith bi pogrešno dopustio ove — sad se preusmjeravaju/odbijaju točno
    assert firstrun._redirect_target("/ui/setupX") is True       # nije /ui/setup ni /ui/setup/*
    assert firstrun._redirect_target("/ui/setup") is False
    assert firstrun._redirect_target("/loginfoo") is False       # nije nav (ne /ui/, ne /, ne /obveze)
    assert firstrun._redirect_target("/login") is False
    assert firstrun._redirect_target("/") is True


def test_create_first_owner_atomic_rejects_second(spine):
    firstrun.create_first_owner(spine, "ana", "TajnaLoz1!")
    import pytest
    with pytest.raises(ValueError):
        firstrun.create_first_owner(spine, "boris", "DrugaLoz2!")
    assert spine.read().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_setup_owner_password_too_short_422(spine, cfg):
    c = _client(spine, cfg)
    r = c.post("/setup/owner", json={"username": "ana", "password": "kratko"})  # 6 < 8
    assert r.status_code == 422  # pydantic validacija


def test_setup_page_redirects_when_users_exist(spine, cfg):
    add_user(spine, "ana", "pw")
    c = _client(spine, cfg)
    r = c.get("/ui/setup")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_preflight_reduced_when_onboarding(spine, cfg):
    c = _client(spine, cfg)
    d = c.get("/preflight").json()  # nema korisnika → reducirano
    assert d["state"]["os"] is None and d["state"]["gpu"] is None
    dd = next(r for r in d["requirements"] if r["key"] == "data_dir")
    assert "/" not in dd["detalj"]  # nema pune putanje
