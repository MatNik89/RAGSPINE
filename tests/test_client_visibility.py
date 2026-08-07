"""Vidljivost klijenata po radniku: business logika + endpoint enforcement + chat."""
from fastapi.testclient import TestClient

from atlas.business import client_visibility as cv
from atlas.business.acl import Actor
from atlas.web.api import create_app
from atlas.web.deps import add_user


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
    from atlas.rag import client_context
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


# --- IDOR: sekundarni client_id endpointi (P0-auth fold) ---

def _restrict(c, spine, owner_tok, worker_name, client_ids):
    wid = _uid(spine, worker_name)
    c.post(f"/workers/{wid}/visibility", json={"sees_all": False, "client_ids": client_ids},
           headers=_h(owner_tok))
    return wid


def test_idor_notes_add_and_read_blocked(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    a, b = _mk_clients(spine, "Alfa", "Beta")
    _restrict(c, spine, owner, "boris", [a])
    # write na tuđeg (Beta) → 403
    assert c.post("/notes", json={"client_id": b, "body": "x"}, headers=_h(worker)).status_code == 403
    # read tuđeg → 403
    assert c.get(f"/notes?client_id={b}", headers=_h(worker)).status_code == 403
    # bulk read (bez client_id) ne smije procuriti Betine bilješke
    with spine.write() as conn:
        conn.execute("INSERT INTO notes(client_id, author, body) VALUES(?,?,?)", (b, "ana", "tajna"))
        conn.execute("INSERT INTO notes(client_id, author, body) VALUES(?,?,?)", (a, "ana", "javno"))
    rows = c.get("/notes", headers=_h(worker)).json()
    assert all(r["client_id"] == a for r in rows)


def test_idor_messaging_send_blocked(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    a, b = _mk_clients(spine, "Alfa", "Beta")
    _restrict(c, spine, owner, "boris", [a])
    r = c.post("/messaging/send", json={"client_id": b, "subject": "s", "body": "x", "dry_run": True},
               headers=_h(worker))
    assert r.status_code == 403


def test_messaging_campaign_admin_only(spine, cfg):
    c = _client(spine, cfg)
    _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    r = c.post("/messaging/campaign",
               json={"filter": "all_active", "subject": "s", "body": "x", "dry_run": True},
               headers=_h(worker))
    assert r.status_code == 403


def test_idor_cjenik_usporedba_blocked(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    a, b = _mk_clients(spine, "Alfa", "Beta")
    _restrict(c, spine, owner, "boris", [a])
    assert c.get(f"/cjenik/usporedba/{b}", headers=_h(worker)).status_code == 403


def test_discover_admin_only(spine, cfg):
    c = _client(spine, cfg)
    _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    assert c.get("/clients/discover?folder_id=1", headers=_h(worker)).status_code == 403
    assert c.post("/clients/discover/commit", json={"folder_id": 1, "items": []},
                  headers=_h(worker)).status_code == 403


# --- RAG retrieval ne smije procuriti dokumente skrivenog klijenta (Codex HIGH) ---

def test_retrieval_filters_hidden_client_docs(spine):
    from atlas.rag import retrieval
    a, b = _mk_clients(spine, "Alfa", "Beta")
    with spine.write() as conn:
        # uredski dokument (client_id NULL) + po jedan klijentski
        conn.execute("INSERT INTO documents(id,title,doc_type,client_id,org_id) VALUES(1,'Ured PDV','propis',NULL,1)")
        conn.execute("INSERT INTO documents(id,title,doc_type,client_id,org_id) VALUES(2,'Alfa PDV','racun',?,1)", (a,))
        conn.execute("INSERT INTO documents(id,title,doc_type,client_id,org_id) VALUES(3,'Beta PDV','racun',?,1)", (b,))
        for did in (1, 2, 3):
            conn.execute("INSERT INTO chunks(id,doc_id,seq,text,title) VALUES(?,?,0,?,?)",
                         (did, did, "pdv stopa porez", f"doc{did}"))
    # restringiran na Alfa → vidi ured + Alfa, NE Beta
    got = {h.doc_id for h in retrieval.search(spine, "pdv", org_id=1, visible_client_ids={a})}
    assert got == {1, 2}
    # bez ograničenja (None) → sve
    allv = {h.doc_id for h in retrieval.search(spine, "pdv", org_id=1, visible_client_ids=None)}
    assert allv == {1, 2, 3}
    # prazan skup → samo uredski
    office = {h.doc_id for h in retrieval.search(spine, "pdv", org_id=1, visible_client_ids=set())}
    assert office == {1}


def test_expiry_and_notifications_filtered_for_restricted(spine, cfg):
    c = _client(spine, cfg)
    owner = _tok(c, spine, "ana")
    worker = _tok(c, spine, "boris")
    a, b = _mk_clients(spine, "Alfa", "Beta")
    _restrict(c, spine, owner, "boris", [a])
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=5)).isoformat()
    with spine.write() as conn:
        conn.execute("INSERT INTO expiry_items(client_id,kind,label,expires) VALUES(?,?,?,?)", (a,"x","Alfa isteče",soon))
        conn.execute("INSERT INTO expiry_items(client_id,kind,label,expires) VALUES(?,?,?,?)", (b,"x","Beta isteče",soon))
        conn.execute("INSERT INTO notifications(kind,body,client_id) VALUES('x','za Alfa',?)", (a,))
        conn.execute("INSERT INTO notifications(kind,body,client_id) VALUES('x','za Beta',?)", (b,))
        conn.execute("INSERT INTO notifications(kind,body,client_id) VALUES('x','uredska',NULL)")
    exp = c.get("/expiry", headers=_h(worker)).json()
    assert {r["client_id"] for r in exp} == {a}
    notif = c.get("/notifications.json", headers=_h(worker)).json()
    cids = {r["client_id"] for r in notif}
    assert b not in cids and a in cids and None in cids  # uredska (NULL) ostaje
