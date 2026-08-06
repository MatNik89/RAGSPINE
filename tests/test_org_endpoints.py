"""Faza D spojnog tkiva: /org members + /wiki + /skills endpointi i ekrani."""
from fastapi.testclient import TestClient

from ragspine.knowledge import wiki
from ragspine.web.api import create_app
from ragspine.web.deps import add_user
from tests.conftest import complete_setup


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _tok(c, spine, username, password="pw", role="radnik"):
    add_user(spine, username, password, role)
    complete_setup(spine)
    return c.post("/auth/login", json={"username": username, "password": password}).json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_owner_adds_member_and_changes_role(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")          # prvi login = owner
    add_user(spine, "boris", "pw2")
    r = c.post("/org/members", json={"username": "boris", "role": "member"}, headers=_h(owner))
    assert r.status_code == 200
    members = c.get("/org", headers=_h(owner)).json()["members"]
    boris = next(m for m in members if m["username"] == "boris")
    r = c.post(f"/org/members/{boris['user_id']}/role", json={"role": "admin"}, headers=_h(owner))
    assert r.status_code == 200
    members = c.get("/org", headers=_h(owner)).json()["members"]
    assert next(m for m in members if m["username"] == "boris")["role"] == "admin"


def test_member_cannot_manage_members(spine, cfg):
    c = _client(spine, cfg)
    _tok(c, spine, "ana")
    member = _tok(c, spine, "boris")       # drugi login = member
    add_user(spine, "cvita", "pw3")
    r = c.post("/org/members", json={"username": "cvita"}, headers=_h(member))
    assert r.status_code == 403


def test_add_member_cannot_upsert_demote_owner(spine, cfg):
    """Codex nalaz: 'dodaj člana' s postojećim članom NE smije biti upsert uloge —
    admin bi tako degradirao ownera zaobilazeći guardove promjene uloge."""
    c = _client(spine, cfg)
    _tok(c, spine, "ana")                         # owner
    admin = _tok(c, spine, "boris", role="admin")  # org admin
    r = c.post("/org/members", json={"username": "ana", "role": "member"}, headers=_h(admin))
    assert r.status_code == 409
    members = c.get("/org", headers=_h(admin)).json()["members"]
    assert next(m for m in members if m["username"] == "ana")["role"] == "owner"


def test_last_owner_cannot_be_demoted(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    me = c.get("/org", headers=_h(owner)).json()["members"][0]
    r = c.post(f"/org/members/{me['user_id']}/role", json={"role": "member"}, headers=_h(owner))
    assert r.status_code == 400


def test_admin_cannot_touch_owner_role(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    admin = _tok(c, spine, "boris", role="admin")  # sistemski admin → org admin
    me = c.get("/org", headers=_h(owner)).json()["members"][0]
    r = c.post(f"/org/members/{me['user_id']}/role", json={"role": "member"}, headers=_h(admin))
    assert r.status_code == 403


def test_wiki_list_get_and_lock(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    org_id = c.get("/org", headers=_h(owner)).json()["org"]["id"]
    with spine.write() as conn:
        conn.execute("INSERT INTO wiki_pages(org_id,type,title,slug,body) "
                     "VALUES(?,?,?,?,?)", (org_id, "process", "JOPPD postupak", "process-joppd", "tijelo"))
    pages = c.get("/wiki", headers=_h(owner)).json()
    assert pages[0]["slug"] == "process-joppd"
    page = c.get("/wiki/process-joppd", headers=_h(owner)).json()
    assert page["body"] == "tijelo"
    assert c.post("/wiki/process-joppd/lock", json={"locked": True}, headers=_h(owner)).status_code == 200
    assert wiki.get_page(spine, org_id, "process-joppd")["locked"] == 1
    member = _tok(c, spine, "boris")
    assert c.post("/wiki/process-joppd/lock", json={"locked": False}, headers=_h(member)).status_code == 403


def test_wiki_is_org_scoped(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    with spine.write() as conn:
        conn.execute("INSERT INTO wiki_pages(org_id,type,title,slug,body) "
                     "VALUES(999,'note','Tudje','note-tudje','tajna')")
    assert c.get("/wiki/note-tudje", headers=_h(owner)).status_code == 404
    assert c.get("/wiki", headers=_h(owner)).json() == []


def test_skill_crud_and_status(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    r = c.post("/skills", json={"name": "JOPPD predaja", "trigger": "joppd",
                                "steps": "1. ePorezna"}, headers=_h(owner))
    sid = r.json()["id"]
    r = c.post(f"/skills/{sid}", json={"steps": "1. ePorezna\n2. Provjeri"}, headers=_h(owner))
    assert r.json()["version"] == 2
    assert c.post(f"/skills/{sid}/status", json={"status": "active"}, headers=_h(owner)).status_code == 200
    lst = c.get("/skills", headers=_h(owner)).json()
    assert lst[0]["status"] == "active"


def test_private_skill_hidden_from_other_member(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    member = _tok(c, spine, "boris")
    c.post("/skills", json={"name": "Moj privatni", "visibility": "private"}, headers=_h(owner))
    assert [s["name"] for s in c.get("/skills", headers=_h(member)).json()] == []
    assert [s["name"] for s in c.get("/skills", headers=_h(owner)).json()] == ["Moj privatni"]


def test_member_cannot_edit_foreign_skill(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    member = _tok(c, spine, "boris")
    sid = c.post("/skills", json={"name": "Org skill", "visibility": "org"},
                 headers=_h(owner)).json()["id"]
    assert c.post(f"/skills/{sid}", json={"steps": "x"}, headers=_h(member)).status_code == 403


def test_ui_pages_render(spine, cfg):
    c = _client(spine, cfg)
    tok = _tok(c, spine, "ana")
    c.cookies.set("ragspine_token", tok)
    for path in ("/ui/org", "/ui/wiki", "/ui/skills", "/ui/postavke"):
        r = c.get(path)
        assert r.status_code == 200, path
    assert "/ui/skills" in c.get("/ui/postavke").text
