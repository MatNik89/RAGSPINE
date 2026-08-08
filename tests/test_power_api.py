"""Faza 4 T3: Postavke→Napajanje API + scheduler poller."""
from fastapi.testclient import TestClient

from atlas.business import power, tenancy
from atlas.ops import jobs
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _admin(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _member(spine, cfg, c):
    add_user(spine, "radnik", "pw")
    tok = c.post("/auth/login", json={"username": "radnik", "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username='radnik'").fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, "member")
    return {"Authorization": f"Bearer {tok}"}


def test_config_admin_only(spine, cfg):
    c, ha = _admin(spine, cfg)
    hm = _member(spine, cfg, c)
    assert c.get("/napajanje/config", headers=hm).status_code == 403
    assert c.post("/napajanje/config", headers=hm,
                  json={"nut_host": "192.168.1.5"}).status_code == 403
    assert c.get("/napajanje/config", headers=ha).status_code == 200


def _role_user(spine, cfg, c, username, role):
    add_user(spine, username, "pw")
    tok = c.post("/auth/login", json={"username": username, "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, role)
    return {"Authorization": f"Bearer {tok}"}


def test_mutations_require_owner_not_just_admin(spine, cfg):
    # Codex T3 HIGH: strojno napajanje je globalno -> samo owner smije mijenjati/armati
    c, ho = _admin(spine, cfg)  # gazda = owner (prvi korisnik)
    ha = _role_user(spine, cfg, c, "admin1", "admin")
    assert c.post("/napajanje/config", headers=ha, json={"nut_host": "192.168.1.5"}).status_code == 403
    assert c.post("/napajanje/arm", headers=ha, json={"armed": True}).status_code == 403
    assert c.get("/napajanje/status", headers=ha).status_code == 200  # čitanje ostaje admin
    assert c.post("/napajanje/config", headers=ho, json={"nut_host": "192.168.1.5"}).status_code == 200


def test_config_cannot_arm_and_is_audited(spine, cfg):
    c, ho = _admin(spine, cfg)
    # 'armed' u config tijelu se ignorira (pydantic drop) — naoružavanje samo /arm
    c.post("/napajanje/config", headers=ho, json={"armed": True, "nut_host": "192.168.1.5"})
    assert power.get_config(spine)["armed"] is False
    row = spine.read().execute(
        "SELECT 1 FROM audit_log WHERE action='napajanje_config'").fetchone()
    assert row is not None  # promjena uvjeta gašenja auditirana


def test_config_save_and_get(spine, cfg):
    c, ha = _admin(spine, cfg)
    r = c.post("/napajanje/config", headers=ha, json={
        "enabled": True, "nut_host": "192.168.1.5", "ups_name": "apc",
        "on_battery_seconds": 90})
    assert r.status_code == 200
    cfg2 = c.get("/napajanje/config", headers=ha).json()
    assert cfg2["nut_host"] == "192.168.1.5" and cfg2["ups_name"] == "apc"
    assert cfg2["on_battery_seconds"] == 90 and cfg2["armed"] is False


def test_config_rejects_public_host(spine, cfg):
    c, ha = _admin(spine, cfg)
    assert c.post("/napajanje/config", headers=ha,
                  json={"nut_host": "8.8.8.8"}).status_code == 400


def test_plan_is_dry_run_no_shutdown(spine, cfg):
    from atlas.business import devices
    c, ha = _admin(spine, cfg)
    devices.add_device(spine, "radna-stanica", "PC1", user="a", host="192.168.1.10",
                       caps={"shutdown_order": 1})
    r = c.get("/napajanje/plan", headers=ha)
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["steps"]]
    assert names == ["PC1", "server"]  # samo pregled, ništa se ne gasi


def test_arm_toggles_armed(spine, cfg):
    c, ha = _admin(spine, cfg)
    assert power.get_config(spine)["armed"] is False
    assert c.post("/napajanje/arm", headers=ha, json={"armed": True}).status_code == 200
    assert power.get_config(spine)["armed"] is True
    assert c.get("/napajanje/config", headers=_member(spine, cfg, c)).status_code == 403  # ostaje admin-only


def test_status_endpoint_fail_closed_without_ups(spine, cfg):
    c, ha = _admin(spine, cfg)
    r = c.get("/napajanje/status", headers=ha)  # bez NUT servera
    assert r.status_code == 200 and r.json()["ok"] is False


def test_ui_napajanje_page(spine, cfg):
    c, ha = _admin(spine, cfg)
    # cookie-login za HTML rutu
    c.post("/auth/login", json={"username": "gazda", "password": "pw"})
    r = c.get("/ui/napajanje", headers=ha)
    assert r.status_code == 200 and "Napajanje" in r.text


def test_poll_job_calls_evaluate_only_when_enabled(spine, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(power, "evaluate",
                        lambda *a, **k: calls.append(1) or {"status": "OL", "shutdown": False})
    jobs.power_job(spine, cfg)  # enabled default False
    assert calls == []
    power.save_config(spine, enabled=True, nut_host="192.168.1.5")
    jobs.power_job(spine, cfg)
    assert calls == [1]


def test_poll_job_registered_in_defaults(spine, cfg):
    from atlas.ops.scheduler import Scheduler
    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    assert "power" in [j.name for j in sched.jobs]
