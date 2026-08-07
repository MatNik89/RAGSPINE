from fastapi.testclient import TestClient
from atlas.web.api import create_app
from atlas.web.deps import COOKIE_NAME, add_user

def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))

def _auth_cookie(client):
    """Parsani cookie iz jar-a (ima .secure flag) — robusno preko httpx/starlette
    verzija; novije verzije ne izlažu 'set-cookie' kroz r.headers.get()."""
    for ck in client.cookies.jar:
        if ck.name == COOKIE_NAME:
            return ck
    return None

def test_health_no_auth(spine, cfg):
    r = _client(spine, cfg).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

def test_login_and_protected(spine, cfg):
    add_user(spine, "ana", "tajna")
    c = _client(spine, cfg)
    assert c.get("/v1/models").status_code == 401
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.get("/v1/models", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["object"] == "list"

def test_bad_login(spine, cfg):
    add_user(spine, "ana", "tajna")
    assert _client(spine, cfg).post("/auth/login",
        json={"username": "ana", "password": "x"}).status_code == 401

def test_unknown_user_login_401(spine, cfg):
    # Timing-side-channel fix: unknown username must still run the dummy
    # hash comparison and return the same 401 as a wrong password.
    add_user(spine, "ana", "tajna")
    r = _client(spine, cfg).post("/auth/login",
        json={"username": "ne-postoji", "password": "bilo-sto"})
    assert r.status_code == 401

def test_login_cookie_not_secure_by_default(spine, cfg):
    add_user(spine, "ana", "tajna")
    c = _client(spine, cfg)
    c.post("/auth/login", data={"username": "ana", "password": "tajna"}, follow_redirects=False)
    ck = _auth_cookie(c)
    assert ck is not None and not ck.secure

def test_login_cookie_secure_when_https_only(spine, cfg):
    add_user(spine, "ana", "tajna")
    cfg.https_only = True
    # https base_url: Secure cookie httpx prihvaća u jar samo preko https konteksta
    c = TestClient(create_app(spine, cfg), base_url="https://testserver")
    c.post("/auth/login", data={"username": "ana", "password": "tajna"}, follow_redirects=False)
    ck = _auth_cookie(c)
    assert ck is not None and ck.secure

def test_malformed_login_body_400(spine, cfg):
    c = _client(spine, cfg)
    r = c.post("/auth/login", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 400

def test_empty_login_body_400(spine, cfg):
    c = _client(spine, cfg)
    assert c.post("/auth/login", json={}).status_code == 400


def test_login_rehasha_legacy_200k_hash(spine, cfg):
    import hashlib
    salt = b"\x01" * 16
    h = hashlib.pbkdf2_hmac("sha256", b"tajna", salt, 200_000)
    legacy = f"{salt.hex()}${h.hex()}"
    with spine.write() as c:
        c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,?,?)",
                  ("stari", legacy, "owner"))
    c2 = _client(spine, cfg)
    r = c2.post("/auth/login", json={"username": "stari", "password": "tajna"})
    assert r.status_code == 200
    row = spine.read().execute(
        "SELECT pw_hash FROM users WHERE username=?", ("stari",)).fetchone()
    assert row["pw_hash"].startswith("pbkdf2$600000$")
    # prijava radi i s novim hashom
    assert c2.post("/auth/login",
                   json={"username": "stari", "password": "tajna"}).status_code == 200
