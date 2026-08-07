import threading

from fastapi.testclient import TestClient

from atlas.browser.bridge import Bridge
from atlas.web.api import create_app
from atlas.web.deps import add_user


def test_enqueue_next_cmd_post_result_roundtrip():
    b = Bridge()
    received = {}

    def caller():
        cmd_id = b.enqueue({"action": "navigate", "url": "https://x"})
        received["result"] = b.wait_result(cmd_id, timeout=2)

    def extension():
        cmd = b.next_cmd(timeout=2)
        assert cmd is not None
        assert cmd["action"] == "navigate"
        b.post_result(cmd["cmd_id"], {"ok": 1})

    t_ext = threading.Thread(target=extension)
    t_call = threading.Thread(target=caller)
    t_ext.start()
    t_call.start()
    t_ext.join(timeout=3)
    t_call.join(timeout=3)
    assert received["result"] == {"ok": 1}


def test_wait_result_timeout_returns_none():
    b = Bridge()
    cmd_id = b.enqueue({"action": "click"})
    assert b.wait_result(cmd_id, timeout=0.5) is None


def test_next_cmd_empty_queue_returns_none():
    b = Bridge()
    assert b.next_cmd(timeout=0.2) is None


def test_post_result_unknown_cmd_id_is_noop():
    b = Bridge()
    b.post_result("nonexistent", {"x": 1})
    assert b._results == {}


def test_post_result_after_wait_timeout_leaves_no_orphan():
    b = Bridge()
    cmd_id = b.enqueue({"action": "click"})
    assert b.wait_result(cmd_id, timeout=0.2) is None
    b.post_result(cmd_id, {"late": True})
    assert b._results == {}
    assert b._events == {}


def test_api_browser_cmd_empty_204(spine, cfg):
    app = create_app(spine, cfg)
    app.state.bridge.cmd_timeout = 0.2
    c = TestClient(app)
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.get("/browser/cmd", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 204
    assert r.content == b""


def test_api_browser_cmd_requires_auth(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    assert c.get("/browser/cmd").status_code == 401


def test_api_browser_result_ok(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.post("/browser/result", json={"cmd_id": "abc", "result": {"ok": 1}}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_api_browser_status(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.get("/browser/status", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json() == {"pending": 0}


def test_api_browser_run_roundtrip(spine, cfg):
    app = create_app(spine, cfg)
    c = TestClient(app)
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}
    bridge = app.state.bridge

    def responder():
        cmd = bridge.next_cmd(timeout=2)
        assert cmd is not None
        bridge.post_result(cmd["cmd_id"], {"ok": 1})

    t = threading.Thread(target=responder)
    t.start()
    r = c.post("/browser/run", json={"action": "read"}, headers=headers)
    t.join(timeout=3)
    assert r.status_code == 200
    assert r.json() == {"ok": 1}


def test_api_browser_run_timeout(spine, cfg):
    app = create_app(spine, cfg)
    app.state.bridge.run_timeout = 0.3
    c = TestClient(app)
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}
    r = c.post("/browser/run", json={"action": "read"}, headers=headers)
    assert r.status_code == 504
    assert r.json() == {"error": "timeout"}
