"""Faza 5 T5: bootstrap instalacija agenta (config + autostart) + /postavi-agent."""
import json

import pytest
from fastapi.testclient import TestClient

from atlas.agent import install
from atlas.business import tenancy
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def test_write_config_persists_token_and_url(tmp_path):
    p = tmp_path / "agent.json"
    cfg = install.write_config(str(p), "https://server.lan:8443", "1.tajna")
    assert cfg["server_url"] == "https://server.lan:8443" and cfg["token"] == "1.tajna"
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["token"] == "1.tajna" and on_disk["program_map"] == {}


def test_write_config_is_owner_only_0600(tmp_path):
    import os
    import stat
    if not hasattr(os, "getuid"):  # POSIX-only provjera modea
        return
    p = tmp_path / "agent.json"
    install.write_config(str(p), "https://server.lan:8443", "tajna")
    mode = stat.S_IMODE(os.stat(str(p)).st_mode)
    assert mode == 0o600  # token nikad svima čitljiv


def test_install_writes_config_and_unit_and_enables_linux(tmp_path):
    calls = []
    cfg_path = tmp_path / "a.json"
    unit_dir = tmp_path / "systemd"
    install.install("https://s.lan:8443", "1.tok", config_path=str(cfg_path),
                    python_exe="/usr/bin/python3", runner=lambda a: calls.append(a),
                    platform="linux", unit_dir=str(unit_dir))
    assert cfg_path.exists()
    unit = (unit_dir / "atlas-agent.service").read_text(encoding="utf-8")
    assert "/usr/bin/python3 -m atlas.agent" in unit and str(cfg_path) in unit
    assert calls == [["systemctl", "--user", "enable", "--now", "atlas-agent.service"]]


def test_main_cli_writes_config(tmp_path):
    p = tmp_path / "cli.json"
    install.main(["--server", "https://s.lan:8443", "--token", "9.tok",
                  "--config", str(p), "--no-autostart"])
    assert json.loads(p.read_text(encoding="utf-8"))["token"] == "9.tok"


def test_write_config_rejects_cleartext_http(tmp_path):
    with pytest.raises(ValueError):
        install.write_config(str(tmp_path / "a.json"), "http://server.lan", "1.t")


def test_autostart_argv_is_arglist_no_shell():
    argv = install.autostart_argv("/usr/bin/python3", "/home/ana/agent.json", platform="linux")
    assert isinstance(argv, list)
    assert not any(";" in a or "&&" in a or "|" in a for a in argv)
    win = install.autostart_argv("C:\\py.exe", "C:\\agent.json", platform="win")
    assert win[0] == "schtasks" and "/F" in win  # idempotentno (force overwrite)


def test_register_autostart_runs_argv(tmp_path):
    calls = []
    install.register_autostart("/usr/bin/python3", str(tmp_path / "a.json"),
                               runner=lambda argv: calls.append(argv), platform="linux")
    assert len(calls) == 1 and isinstance(calls[0], list)


def _owner(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    c.post("/auth/login", json={"username": "gazda", "password": "pw"})  # cookie za HTML
    return c, {"Authorization": f"Bearer {tok}"}


def test_postavi_agent_page_owner_only(spine, cfg):
    c, ho = _owner(spine, cfg)
    add_user(spine, "admin1", "pw")
    ta = c.post("/auth/login", json={"username": "admin1", "password": "pw"}).json()["token"]
    uid = spine.read().execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    tenancy.add_member(spine, tenancy.default_org_id(spine), uid, "admin")
    assert c.get("/postavi-agent", headers={"Authorization": f"Bearer {ta}"}).status_code == 403
    r = c.get("/postavi-agent", headers=ho)
    assert r.status_code == 200 and "agent" in r.text.lower()
