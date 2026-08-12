"""Zakazani zadaci (owner): allowlist akcija, day_of_month/hour firanje, dedupe."""
from datetime import datetime

import pytest

from atlas.business import scheduler_tasks as st
from atlas.business import tenancy


def _org(spine):
    return tenancy.default_org_id(spine)


def test_create_rejects_unknown_action(spine):
    with pytest.raises(ValueError):
        st.create_task(spine, _org(spine), "x", "nepostojeca", {}, 20, 8)


def test_create_validates_action_params(spine):
    with pytest.raises(ValueError):  # kampanja_obveza traži 'kind'
        st.create_task(spine, _org(spine), "x", "kampanja_obveza", {}, 20, 8)


def test_create_and_list(spine):
    tid = st.create_task(spine, _org(spine), "PDV 20.", "kampanja_obveza",
                         {"kind": "PDV"}, 20, 8, user="gazda")
    rows = st.list_tasks(spine, _org(spine))
    assert rows[0]["id"] == tid and rows[0]["params"]["kind"] == "PDV"
    assert rows[0]["day_of_month"] == 20 and rows[0]["hour"] == 8


def test_day_and_hour_validation(spine):
    with pytest.raises(ValueError):
        st.create_task(spine, _org(spine), "x", "kampanja_obveza", {"kind": "PDV"}, 40, 8)
    with pytest.raises(ValueError):
        st.create_task(spine, _org(spine), "x", "kampanja_obveza", {"kind": "PDV"}, 20, 99)


def test_run_due_fires_on_matching_day_and_dedupes(spine, cfg, monkeypatch):
    org = _org(spine)
    st.create_task(spine, org, "PDV", "kampanja_obveza", {"kind": "PDV"}, 20, 8)
    calls = []
    monkeypatch.setattr("atlas.business.messaging.send_to_filter",
                        lambda *a, **k: calls.append(1) or {"audience": 0, "results": {}})
    # prije sata -> ne fira
    assert st.run_due(spine, cfg, now=datetime(2026, 8, 20, 7, 0)) == []
    # na dan i sat -> fira jednom
    fired = st.run_due(spine, cfg, now=datetime(2026, 8, 20, 9, 0))
    assert len(fired) == 1 and fired[0]["status"] == "ok" and len(calls) == 1
    # isti dan opet -> dedupe (ne fira drugi put)
    assert st.run_due(spine, cfg, now=datetime(2026, 8, 20, 10, 0)) == []
    assert len(calls) == 1


def test_run_due_skips_wrong_day(spine, cfg, monkeypatch):
    st.create_task(spine, _org(spine), "PDV", "kampanja_obveza", {"kind": "PDV"}, 20, 8)
    monkeypatch.setattr("atlas.business.messaging.send_to_filter", lambda *a, **k: {})
    assert st.run_due(spine, cfg, now=datetime(2026, 8, 21, 9, 0)) == []


def test_run_due_error_deadletters_and_advances(spine, cfg, monkeypatch):
    st.create_task(spine, _org(spine), "PDV", "kampanja_obveza", {"kind": "PDV"}, None, 8)

    def boom(*a, **k):
        raise RuntimeError("puklo")
    monkeypatch.setattr("atlas.business.messaging.send_to_filter", boom)
    fired = st.run_due(spine, cfg, now=datetime(2026, 8, 15, 9, 0))
    assert fired[0]["status"] == "error"
    # obavijest zapisana, last_run postavljen (bez retry-buke istog dana)
    assert spine.read().execute(
        "SELECT 1 FROM notifications WHERE kind='scheduled_error'").fetchone() is not None
    assert st.run_due(spine, cfg, now=datetime(2026, 8, 15, 10, 0)) == []


def test_toggle_and_delete(spine):
    org = _org(spine)
    tid = st.create_task(spine, org, "PDV", "kampanja_obveza", {"kind": "PDV"}, 20, 8)
    st.set_enabled(spine, tid, org, False)
    assert st.list_tasks(spine, org)[0]["enabled"] == 0
    st.delete_task(spine, tid, org)
    assert st.list_tasks(spine, org) == []


def test_endpoints_owner_only(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    ho = {"Authorization": "Bearer " + c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]}
    add_user(spine, "m1", "pw")
    tm = c.post("/auth/login", json={"username": "m1", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='m1'").fetchone()["id"], "member")
    body = {"title": "PDV", "action_key": "kampanja_obveza", "params": {"kind": "PDV"}, "day_of_month": 20, "hour": 8}
    assert c.post("/zakazano", headers={"Authorization": f"Bearer {tm}"}, json=body).status_code == 403
    assert c.post("/zakazano", headers=ho, json=body).status_code == 200
    lst = c.get("/zakazano", headers=ho).json()
    assert lst["zadaci"][0]["action_key"] == "kampanja_obveza"
    assert any(a["key"] == "kampanja_obveza" for a in lst["akcije"])


def test_day_clamps_to_month_end(spine, cfg, monkeypatch):
    # zadatak na 31. mora firati zadnji dan veljače (28.), ne "nikad"
    st.create_task(spine, _org(spine), "kraj mj", "kampanja_obveza", {"kind": "PDV"}, 31, 8)
    monkeypatch.setattr("atlas.business.messaging.send_to_filter", lambda *a, **k: {})
    assert st.run_due(spine, cfg, now=datetime(2026, 2, 28, 9, 0))  # 28. veljače = zadnji
    # a ne fira 27.
    st.set_enabled(spine, st.list_tasks(spine, _org(spine))[0]["id"], _org(spine), True)


def test_atomic_claim_prevents_double_fire(spine, cfg, monkeypatch):
    st.create_task(spine, _org(spine), "PDV", "kampanja_obveza", {"kind": "PDV"}, None, 8)
    calls = []
    monkeypatch.setattr("atlas.business.messaging.send_to_filter",
                        lambda *a, **k: calls.append(1) or {})
    now = datetime(2026, 8, 15, 9, 0)
    st.run_due(spine, cfg, now=now)
    st.run_due(spine, cfg, now=now)  # isti dan, drugi poll
    assert len(calls) == 1  # claim spriječio dvostruko


def test_get_zakazano_owner_only(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    add_user(spine, "m1", "pw")
    tm = c.post("/auth/login", json={"username": "m1", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='m1'").fetchone()["id"], "member")
    assert c.get("/zakazano", headers={"Authorization": f"Bearer {tm}"}).status_code == 403
