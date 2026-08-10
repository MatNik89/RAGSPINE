"""Telegram gateway: split, pairing, auth, handle_update — mockano (bez mreže)."""
from atlas.business import telegram_gateway as tg
from atlas.web.deps import add_user


class FakeTG:
    def __init__(self): self.sent = []; self.answered = []
    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text)); self.markup = reply_markup
    def answer_callback(self, callback_id, text=""): self.answered.append((callback_id, text))


def _seed_user(spine, name="ana"):
    add_user(spine, name, "pw")
    uid = spine.read().execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()["id"]
    from atlas.business import tenancy
    org = tenancy.create_org(spine, "Ured", uid)  # ana owner
    return uid, org


def test_split_short_and_long():
    assert tg.split_message("bok") == ["bok"]
    big = "\n".join(["red %d" % i for i in range(2000)])
    parts = tg.split_message(big, limit=200)
    assert all(len(p) <= 200 for p in parts) and "".join(parts).count("red") == 2000


def test_split_single_overlong_line():
    parts = tg.split_message("x" * 500, limit=100)
    assert len(parts) == 5 and all(len(p) <= 100 for p in parts)


def test_pairing_creates_link_once(spine):
    uid, org = _seed_user(spine)
    token = tg.create_pairing_token(spine, uid, org)
    assert tg._consume_pairing(spine, token, 111, "boris") is True
    link = tg._link_for(spine, 111)
    assert link["user_id"] == uid
    # isti token drugi put ne radi (used)
    assert tg._consume_pairing(spine, token, 222, "x") is False


def test_handle_start_valid_and_invalid(spine, cfg):
    uid, org = _seed_user(spine)
    token = tg.create_pairing_token(spine, uid, org)
    ft = FakeTG()
    tg.handle_update(spine, cfg, {"message": {"chat": {"id": 111, "username": "b", "type": "private"}, "from": {"id": 111}, "text": f"/start {token}"}},
                     answer_fn=None, tg=ft)
    assert "Uparen" in ft.sent[-1][1]
    ft2 = FakeTG()
    tg.handle_update(spine, cfg, {"message": {"chat": {"id": 222, "type": "private"}, "from": {"id": 222}, "text": "/start krivo"}},
                     answer_fn=None, tg=ft2)
    assert "token" in ft2.sent[-1][1].lower()


def test_handle_unpaired_rejected(spine, cfg):
    ft = FakeTG()
    calls = []
    tg.handle_update(spine, cfg, {"message": {"chat": {"id": 999, "type": "private"}, "from": {"id": 999}, "text": "koliko je PDV"}},
                     answer_fn=lambda q, a: calls.append(q) or {"answer": "x"}, tg=ft)
    assert not calls  # answer_fn se NE zove za neuparenog
    assert "upar" in ft.sent[-1][1].lower()


def test_handle_paired_runs_pipeline(spine, cfg):
    uid, org = _seed_user(spine)
    token = tg.create_pairing_token(spine, uid, org)
    tg._consume_pairing(spine, token, 111, "ana")
    ft = FakeTG()
    got = {}
    def answer_fn(q, actor):
        got["q"] = q; got["actor"] = actor.username
        return {"answer": "PDV je 25%"}
    tg.handle_update(spine, cfg, {"message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111}, "text": "koliko je PDV"}},
                     answer_fn=answer_fn, tg=ft)
    assert got["q"] == "koliko je PDV" and got["actor"] == "ana"
    assert ft.sent[-1] == (111, "PDV je 25%")


def test_pending_reply_attaches_keyboard(spine, cfg):
    uid, org = _seed_user(spine)
    tg._consume_pairing(spine, tg.create_pairing_token(spine, uid, org), 111, "ana")
    ft = FakeTG()
    tg.handle_update(spine, cfg,
                     {"message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111},
                                  "text": "dodaj klijenta"}},
                     answer_fn=lambda q, a: {"answer": "Dodat ću.", "pending_token": "TKN"}, tg=ft)
    assert ft.markup["inline_keyboard"][0][0]["callback_data"] == "ok:TKN"


def test_callback_confirms_and_executes(spine, cfg):
    from atlas.business import tenancy
    from atlas.rag import agent
    uid, org = _seed_user(spine)
    tg._consume_pairing(spine, tg.create_pairing_token(spine, uid, org), 111, "ana")
    actor = tenancy.actor_for(spine, org, uid)
    actor.username = "ana"
    token = agent.stash_pending(spine, actor, {"tool": "dodaj_klijenta", "args": {"naziv": "NoviTG"}})
    ft = FakeTG()
    tg.handle_update(spine, cfg,
                     {"callback_query": {"id": "cb1", "data": f"ok:{token}",
                                         "from": {"id": 111},
                                         "message": {"chat": {"id": 111, "type": "private"}}}}, tg=ft,
                     answer_fn=None)
    assert ft.answered and "Izvršeno" in ft.answered[-1][1]
    row = spine.read().execute("SELECT 1 FROM clients WHERE name='NoviTG'").fetchone()
    assert row is not None  # write stvarno izvršen


def test_callback_cancel_removes_pending(spine, cfg):
    from atlas.business import tenancy
    from atlas.rag import agent
    uid, org = _seed_user(spine)
    tg._consume_pairing(spine, tg.create_pairing_token(spine, uid, org), 111, "ana")
    actor = tenancy.actor_for(spine, org, uid); actor.username = "ana"
    token = agent.stash_pending(spine, actor, {"tool": "dodaj_klijenta", "args": {"naziv": "X"}})
    ft = FakeTG()
    tg.handle_update(spine, cfg,
                     {"callback_query": {"id": "cb2", "data": f"no:{token}", "from": {"id": 111},
                                         "message": {"chat": {"id": 111, "type": "private"}}}},
                     tg=ft, answer_fn=None)
    left = spine.read().execute("SELECT 1 FROM agent_pending WHERE token=?", (token,)).fetchone()
    assert left is None  # odustao -> token maknut


def test_callback_from_unpaired_chat_ignored(spine, cfg):
    from atlas.business import tenancy
    from atlas.rag import agent
    uid, org = _seed_user(spine)  # ana uparena na 111
    tg._consume_pairing(spine, tg.create_pairing_token(spine, uid, org), 111, "ana")
    actor = tenancy.actor_for(spine, org, uid); actor.username = "ana"
    token = agent.stash_pending(spine, actor, {"tool": "dodaj_klijenta", "args": {"naziv": "Y"}})
    ft = FakeTG()
    # klik dolazi iz NEUPARENOG chata 777 -> ne smije izvršiti tuđi token
    tg.handle_update(spine, cfg,
                     {"callback_query": {"id": "cb3", "data": f"ok:{token}", "from": {"id": 777},
                                         "message": {"chat": {"id": 777, "type": "private"}}}},
                     tg=ft, answer_fn=None)
    assert spine.read().execute("SELECT 1 FROM clients WHERE name='Y'").fetchone() is None


def test_offset_persistence(spine):
    assert tg._get_offset(spine, "default") == 0
    tg._set_offset(spine, "default", 42)
    assert tg._get_offset(spine, "default") == 42


def _admin(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "pw")
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_telegram_gateway_type_and_no_thread(spine, cfg):
    c, h = _admin(spine, cfg)
    kinds = {t["kind"] for t in c.get("/connector-types", headers=h).json()}
    assert "telegram_gateway" in kinds
    # bez konfiguriranog konektora → nije pokrenut poll-thread
    assert getattr(c.app.state, "tg_thread", None) is None


def test_pairing_route_self_service(spine, cfg):
    c, h = _admin(spine, cfg)
    r = c.post("/telegram/pairing", headers=h)
    assert r.status_code == 200 and r.json()["command"].startswith("/start ")
    # self-service: i radnik smije generirati SVOJ token (veže na sebe, ne admina)
    add_user(spine, "boris", "pw", "radnik")
    wt = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    assert c.post("/telegram/pairing", headers={"Authorization": f"Bearer {wt}"}).status_code == 200


def test_group_chat_rejected(spine, cfg):
    uid, org = _seed_user(spine)
    token = tg.create_pairing_token(spine, uid, org)
    tg._consume_pairing(spine, token, 111, "ana")
    ft = FakeTG(); calls = []
    # grupa: chat.type='group', from.id != chat.id → mora se ignorirati
    tg.handle_update(spine, cfg, {"message": {"chat": {"id": -100, "type": "group"},
                     "from": {"id": 555}, "text": "tajni upit"}},
                     answer_fn=lambda q, a: calls.append(q), tg=ft)
    assert not calls and not ft.sent  # ništa se ne obrađuje ni šalje


def test_expired_token_rejected(spine, cfg):
    uid, org = _seed_user(spine)
    token = tg.create_pairing_token(spine, uid, org)
    # ostari token izvan TTL-a
    with spine.write() as c:
        c.execute("UPDATE telegram_pairing SET created_at=datetime('now','-20 minutes') WHERE token=?", (token,))
    assert tg._consume_pairing(spine, token, 111, "x") is False
    assert tg._link_for(spine, 111) is None
