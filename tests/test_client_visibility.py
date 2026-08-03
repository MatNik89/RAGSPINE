"""Vidljivost klijenata po radniku: business logika + endpoint enforcement + chat."""
from fastapi.testclient import TestClient

from ragspine.business import client_visibility as cv
from ragspine.business.acl import Actor
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _tok(c, spine, username, password="pw", role="radnik"):
    add_user(spine, username, password, role)
    return c.post("/auth/login", json={"username": username, "password": password}).json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _mk_clients(spine, *names):
    ids = []
    with spine.write() as conn:
        for n in names:
            ids.append(conn.execute("INSERT INTO clients(name) VALUES(?)", (n,)).lastrowid)
    return ids


def _uid(spine, username):
    return spine.read().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]


# --- business ---

def test_default_sees_all(spine):
    add_user(spine, "ana", "pw")
    uid = _uid(spine, "ana")
    assert cv.visible_ids(spine, uid, "member") is None
    assert cv.can_see(spine, uid, 123, "member")


def test_restrict_then_can_see_only_selected(spine):
    a, b, cc = _mk_clients(spine, "Alfa", "Beta", "Cezar")
    add_user(spine, "ana", "pw")
    uid = _uid(spine, "ana")
    cv.set_policy(spine, uid, sees_all=False, client_ids=[a, cc])
    assert cv.visible_ids(spine, uid, "member") == {a, cc}
    assert cv.can_see(spine, uid, a, "member")
    assert not cv.can_see(spine, uid, b, "member")


def test_manager_bypasses_restriction(spine):
    a, b = _mk_clients(spine, "Alfa", "Beta")
    add_user(spine, "ana", "pw")
    uid = _uid(spine, "ana")
    cv.set_policy(spine, uid, sees_all=False, client_ids=[a])
    assert cv.visible_ids(spine, uid, "admin") is None  # admin uvijek sve
    assert cv.can_see(spine, uid, b, "owner")


# --- endpoint enforcement ---

def test_clients_list_filtered_for_restricted_worker(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")                 # owner
    worker = _tok(c, spine, "boris")              # member
    a, b = _mk_clients(spine, "Alfa", "Beta")
    # owner ograniči borisu vidljivost na Alfa
    bid = _uid(spine, "boris")
    c.post(f"/workers/{bid}/visibility", json={"sees_all": False, "client_ids": [a]}, headers=_h(owner))
    names = [x["name"] for x in c.get("/clients", headers=_h(worker)).json()]
    assert names == ["Alfa"]
    assert [x["name"] for x in c.get("/clients", headers=_h(owner)).json()] == ["Alfa", "Beta"]


def test_client_get_forbidden_when_not_visible(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    a, b = _mk_clients(spine, "Alfa", "Beta")
    bid = _uid(spine, "boris")
    c.post(f"/workers/{bid}/visibility", json={"sees_all": False, "client_ids": [a]}, headers=_h(owner))
    assert c.get(f"/clients/{a}", headers=_h(worker)).status_code == 200
    assert c.get(f"/clients/{b}", headers=_h(worker)).status_code == 403


def test_worker_endpoints_admin_only(spine, cfg):
    c = _client(spine, cfg)
    _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")              # member
    assert c.get("/workers", headers=_h(worker)).status_code == 403
    assert c.post("/workers/1/visibility", json={"sees_all": True},
                  headers=_h(worker)).status_code == 403


def test_creator_keeps_visibility_of_own_client(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    bid = _uid(spine, "boris")
    c.post(f"/workers/{bid}/visibility", json={"sees_all": False, "client_ids": []}, headers=_h(owner))
    new_id = c.post("/clients", json={"name": "Nova firma", "oib": "12345678903"},
                    headers=_h(worker)).json()["id"]
    assert c.get(f"/clients/{new_id}", headers=_h(worker)).status_code == 200


def test_model_write_admin_only(spine, cfg):
    """Codex #7: preusmjeravanje LLM-a je exfiltracija — samo admin."""
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    payload = {"provider": "ollama", "model": "llama3", "base_url": "", "api_key": "",
               "embed_model": "", "ollama_url": "http://127.0.0.1:11434"}
    assert c.post("/model", json=payload, headers=_h(worker)).status_code == 403
    assert c.post("/model", json=payload, headers=_h(owner)).status_code == 200


# --- chat ---

def test_chat_hides_note_of_invisible_client(spine, cfg):
    from ragspine.rag import client_context
    a, b = _mk_clients(spine, "Alfa", "Beta")
    add_user(spine, "boris", "pw")
    uid = _uid(spine, "boris")
    with spine.write() as conn:
        conn.execute("INSERT INTO notes(client_id, author, body) VALUES(?,?,?)",
                     (b, "ana", "Tajna bilješka za Betu"))
    cv.set_policy(spine, uid, sees_all=False, client_ids=[a])
    restricted = Actor(user_id=uid, org_id=1, role="member", username="boris")
    # upit spominje Betu, ali boris je ne smije vidjeti → resolve vraća None
    assert client_context.resolve_client(spine, "što ima za Beta", actor=restricted) is None
    # bez ograničenja (owner) → resolve radi
    owner = Actor(user_id=1, org_id=1, role="owner", username="ana")
    assert client_context.resolve_client(spine, "što ima za Beta", actor=owner) is not None
