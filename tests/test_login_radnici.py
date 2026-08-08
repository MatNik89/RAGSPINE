"""Faza 2 T1: aktivacijski login, admin-kao-radnik, radnici API."""
from fastapi.testclient import TestClient

from atlas.core.security import jwt_decode
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _tok(c, spine, username, password="pw", role="radnik"):
    add_user(spine, username, password, role)
    complete_setup(spine)
    return c.post("/auth/login", json={"username": username, "password": password}).json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- /login/step (anti-enumeracija) ----------

def test_login_step_unknown_user_is_password(spine, cfg):
    complete_setup(spine)
    r = _client(spine, cfg).post("/login/step", json={"username": "ne-postoji"})
    assert r.status_code == 200 and r.json()["state"] == "password"


def test_login_step_active_user_is_password(spine, cfg):
    add_user(spine, "ana", "tajna")
    complete_setup(spine)
    r = _client(spine, cfg).post("/login/step", json={"username": "ana"})
    assert r.json()["state"] == "password"


def test_login_step_pending_user_is_activate(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,NULL,'radnik')", ("boris",))
    complete_setup(spine)
    r = _client(spine, cfg).post("/login/step", json={"username": "boris"})
    assert r.json()["state"] == "activate"


# ---------- /login/activate ----------

def test_login_activate_happy_path(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,NULL,'radnik')", ("boris",))
    complete_setup(spine)
    c = _client(spine, cfg)
    r = c.post("/login/activate", json={"username": "boris", "password": "novalozinka", "password2": "novalozinka"})
    assert r.status_code == 200
    assert "token" in r.json()
    row = spine.read().execute("SELECT pw_hash FROM users WHERE username='boris'").fetchone()
    assert row["pw_hash"] is not None and row["pw_hash"].startswith("pbkdf2$")
    audit = spine.read().execute("SELECT * FROM audit_log WHERE action='user_activate'").fetchone()
    assert audit is not None and audit["detail"].startswith("user aktiviran ip=")
    # odmah prijavljen — token radi na zaštićenoj ruti
    tok = r.json()["token"]
    assert c.get("/org", headers=_h(tok)).status_code == 200


def test_login_activate_already_active_409(spine, cfg):
    add_user(spine, "ana", "tajna")
    complete_setup(spine)
    r = _client(spine, cfg).post("/login/activate",
        json={"username": "ana", "password": "nova12345", "password2": "nova12345"})
    assert r.status_code == 409


def test_login_activate_race_second_call_409(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,NULL,'radnik')", ("boris",))
    complete_setup(spine)
    cl = _client(spine, cfg)
    r1 = cl.post("/login/activate", json={"username": "boris", "password": "prvaLoz1", "password2": "prvaLoz1"})
    assert r1.status_code == 200
    r2 = cl.post("/login/activate", json={"username": "boris", "password": "drugaLoz2", "password2": "drugaLoz2"})
    assert r2.status_code == 409


def test_login_activate_too_short_422(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,NULL,'radnik')", ("boris",))
    complete_setup(spine)
    r = _client(spine, cfg).post("/login/activate", json={"username": "boris", "password": "kratko", "password2": "kratko"})
    assert r.status_code == 422


def test_login_activate_mismatch_400(spine, cfg):
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,NULL,'radnik')", ("boris",))
    complete_setup(spine)
    r = _client(spine, cfg).post("/login/activate",
        json={"username": "boris", "password": "lozinka1", "password2": "lozinka2"})
    assert r.status_code == 400


def test_login_activate_unknown_user_404(spine, cfg):
    complete_setup(spine)
    r = _client(spine, cfg).post("/login/activate",
        json={"username": "ne-postoji", "password": "lozinka1", "password2": "lozinka1"})
    assert r.status_code == 404


# C1 — napad: neautenticirano preuzimanje pending admin računa kroz /login/activate.
def test_login_activate_blocks_pending_admin_account_takeover(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")
    r = c.post("/radnici", json={"username": "napadnuti-admin", "role": "admin"}, headers=_h(owner_tok))
    assert r.status_code == 200
    # napadač pogodi username (otkriven kroz /login/step "activate" state) i
    # pokuša postaviti svoju sifru bez ikakve autentikacije
    r = c.post("/login/activate",
        json={"username": "napadnuti-admin", "password": "napadacevaLoz1", "password2": "napadacevaLoz1"})
    assert r.status_code == 403
    row = spine.read().execute(
        "SELECT pw_hash FROM users WHERE username='napadnuti-admin'").fetchone()
    assert row["pw_hash"] is None  # racun i dalje netaknut
    # napadac nema token (aktivacija je odbijena) — admin-only ruta i dalje zakljucana
    # (svjez klijent bez ownerova cookieja iz _tok — c i dalje nosi ownerovu sesiju)
    assert _client(spine, cfg).get("/radnici").status_code in (401, 403)
    assert c.post("/auth/login",
        json={"username": "napadnuti-admin", "password": "napadacevaLoz1"}).status_code == 401


def test_login_activate_allows_member_role_self_activation(spine, cfg):
    # kontrolni test: member (radnik) i dalje smije sam sebe aktivirati — samo
    # admin/owner-tier pending racuni su blokirani C1 zastitom.
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")
    c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    r = c.post("/login/activate", json={"username": "boris", "password": "borislozinka1", "password2": "borislozinka1"})
    assert r.status_code == 200


# ---------- Admin-kao-radnik ----------

def test_admin_logs_in_as_pending_worker_with_own_password(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")  # prvi login = owner
    r = c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    assert r.status_code == 200
    r = c.post("/auth/login", json={"username": "boris", "password": "sifra-ane"})
    assert r.status_code == 200
    tok = r.json()["token"]
    payload = jwt_decode(tok, cfg.jwt_secret)
    assert payload["sub"] == "boris"
    assert payload["impersonated_by"] == "ana"
    audit = spine.read().execute(
        "SELECT * FROM audit_log WHERE action='impersonate'").fetchone()
    assert audit is not None
    assert audit["user"] == "ana" and audit["entity"] == "user:boris"
    assert audit["detail"] == "admin ana ušao kao boris"
    # sesija nosi rolu radnika, ne admina — actor.role svjež iz membershipa
    assert c.get("/org", headers=_h(tok)).json()["role"] == "member"


def test_admin_logs_in_as_activated_worker_when_own_password_wrong(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")
    c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    c.post("/login/activate", json={"username": "boris", "password": "boris-svoja", "password2": "boris-svoja"})
    r = c.post("/auth/login", json={"username": "boris", "password": "sifra-ane"})
    assert r.status_code == 200
    payload = jwt_decode(r.json()["token"], cfg.jwt_secret)
    assert payload["impersonated_by"] == "ana"


def test_worker_wrong_password_and_no_admin_match_401(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")
    c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    r = c.post("/auth/login", json={"username": "boris", "password": "nista-od-ovoga"})
    assert r.status_code == 401


def test_own_password_login_has_no_impersonated_claim(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")
    c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    c.post("/login/activate", json={"username": "boris", "password": "boris-svoja", "password2": "boris-svoja"})
    r = c.post("/auth/login", json={"username": "boris", "password": "boris-svoja"})
    payload = jwt_decode(r.json()["token"], cfg.jwt_secret)
    assert "impersonated_by" not in payload


# I1 — napad: timing enumeracija kroz admin-fallback petlju (poznat user + kriva
# lozinka radi vise verify_password poziva nego nepoznat user, sto otkriva
# postojanje racuna kroz latenciju — mora biti isti broj poziva).
def test_admin_fallback_timing_equal_verify_calls_known_vs_unknown(spine, cfg, monkeypatch):
    import atlas.web.api as api_mod
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")   # jedini owner/admin u default orgu
    c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))

    calls = {"n": 0}
    real_verify = api_mod.verify_password

    def counting(pw, stored):
        calls["n"] += 1
        return real_verify(pw, stored)

    monkeypatch.setattr(api_mod, "verify_password", counting)

    calls["n"] = 0
    c.post("/auth/login", json={"username": "boris", "password": "kriva-lozinka"})
    known_wrong_calls = calls["n"]

    calls["n"] = 0
    c.post("/auth/login", json={"username": "ne-postoji-nikad-xyz", "password": "kriva-lozinka"})
    unknown_calls = calls["n"]

    assert known_wrong_calls == unknown_calls
    assert known_wrong_calls >= 2  # vlastita provjera + barem jedan admin-fallback pokusaj


# ---------- Radnici API ----------

def test_radnici_add_list_reset_delete(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana", "sifra-ane")
    r = c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    assert r.status_code == 200
    rid = r.json()["id"]

    lst = c.get("/radnici", headers=_h(owner_tok)).json()
    boris = next(x for x in lst if x["user"] == "boris")
    assert boris["role"] == "member" and boris["aktivan"] is False and boris["device"] is None

    c.post("/login/activate", json={"username": "boris", "password": "borislozinka", "password2": "borislozinka"})
    lst = c.get("/radnici", headers=_h(owner_tok)).json()
    assert next(x for x in lst if x["user"] == "boris")["aktivan"] is True

    r = c.post(f"/radnici/{rid}/reset", headers=_h(owner_tok))
    assert r.status_code == 200
    row = spine.read().execute("SELECT pw_hash FROM users WHERE id=?", (rid,)).fetchone()
    assert row["pw_hash"] is None
    assert spine.read().execute(
        "SELECT 1 FROM audit_log WHERE action='radnik_reset'").fetchone() is not None

    r = c.delete(f"/radnici/{rid}", headers=_h(owner_tok))
    assert r.status_code == 200
    lst = c.get("/radnici", headers=_h(owner_tok)).json()
    assert not any(x["user"] == "boris" for x in lst)
    assert spine.read().execute(
        "SELECT 1 FROM audit_log WHERE action='radnik_remove'").fetchone() is not None


def test_radnici_requires_admin(spine, cfg):
    c = _client(spine, cfg)
    _tok(c, spine, "ana")               # owner
    member_tok = _tok(c, spine, "boris")  # obican clan
    assert c.get("/radnici", headers=_h(member_tok)).status_code == 403
    assert c.post("/radnici", json={"username": "cvita"}, headers=_h(member_tok)).status_code == 403


def test_radnici_admin_cannot_reset_or_delete_owner(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana")
    admin_tok = _tok(c, spine, "boris", role="admin")
    owner_id = c.get("/org", headers=_h(owner_tok)).json()["members"][0]["user_id"]
    assert c.post(f"/radnici/{owner_id}/reset", headers=_h(admin_tok)).status_code == 403
    assert c.delete(f"/radnici/{owner_id}", headers=_h(admin_tok)).status_code == 403


def test_radnici_owner_can_reset_self_when_not_last_owner(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana")
    members = c.get("/org", headers=_h(owner_tok)).json()["members"]
    owner_id = next(m["user_id"] for m in members if m["username"] == "ana")
    boris_tok = _tok(c, spine, "boris")
    boris_id = next(m["user_id"] for m in c.get("/org", headers=_h(owner_tok)).json()["members"]
                    if m["username"] == "boris")
    c.post(f"/org/members/{boris_id}/role", json={"role": "owner"}, headers=_h(owner_tok))
    assert c.post(f"/radnici/{owner_id}/reset", headers=_h(owner_tok)).status_code == 200


# I2 — napad: jedini owner sam sebe obrise/resetira → org bez ijedne uprave,
# trajni lockout bez UI oporavka (nitko ne moze admin-kao-radnik ući nazad).
def test_radnici_last_owner_cannot_reset_self(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana")
    owner_id = c.get("/org", headers=_h(owner_tok)).json()["members"][0]["user_id"]
    r = c.post(f"/radnici/{owner_id}/reset", headers=_h(owner_tok))
    assert r.status_code == 409
    row = spine.read().execute("SELECT pw_hash FROM users WHERE id=?", (owner_id,)).fetchone()
    assert row["pw_hash"] is not None  # owner ostaje funkcionalan


def test_radnici_last_owner_cannot_delete_self(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana")
    owner_id = c.get("/org", headers=_h(owner_tok)).json()["members"][0]["user_id"]
    r = c.delete(f"/radnici/{owner_id}", headers=_h(owner_tok))
    assert r.status_code == 409
    assert c.get("/org", headers=_h(owner_tok)).status_code == 200  # jos uvijek clan/owner


def test_radnici_add_duplicate_username_409(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana")
    c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    r = c.post("/radnici", json={"username": "boris", "role": "member"}, headers=_h(owner_tok))
    assert r.status_code == 409


def test_radnici_add_invalid_role_400(spine, cfg):
    c = _client(spine, cfg)
    owner_tok = _tok(c, spine, "ana")
    r = c.post("/radnici", json={"username": "boris", "role": "owner"}, headers=_h(owner_tok))
    assert r.status_code == 400
