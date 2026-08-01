from fastapi.testclient import TestClient

from ragspine.browser import agent
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def test_available_false_without_browser_use():
    assert agent.available() is False


def test_run_task_returns_error_dict_without_raising(cfg):
    result = agent.run_task(cfg, "otvori google")
    assert result["error"] == "browser-use nije instaliran"
    assert "hint" in result


def _auth_headers(spine, cfg):
    add_user(spine, "ana", "tajna")
    c = TestClient(create_app(spine, cfg))
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_browser_agent_endpoint_requires_auth(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    r = c.post("/browser/agent", json={"task": "otvori google"})
    assert r.status_code == 401


def test_browser_agent_endpoint_returns_error_without_browser_use(spine, cfg):
    c, headers = _auth_headers(spine, cfg)
    r = c.post("/browser/agent", json={"task": "otvori google"}, headers=headers)
    assert r.status_code == 200
    assert "error" in r.json()
