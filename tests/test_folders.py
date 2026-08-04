import os

import pytest
from fastapi.testclient import TestClient

from ragspine.business import folders
from ragspine.config import Config, set_config
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _cfg_with_roots(tmp_path, roots):
    monkey_env = {"RAGSPINE_DATA_DIR": str(tmp_path / "data"),
                  "RAGSPINE_MOUNT_ROOTS": ",".join(roots)}
    old = dict(os.environ)
    os.environ.update(monkey_env)
    try:
        cfg = Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)
    set_config(cfg)
    return cfg


# ---------- config ----------

def test_config_parses_mount_roots(tmp_path):
    a, b = tmp_path / "nas", tmp_path / "win"
    a.mkdir(); b.mkdir()
    cfg = _cfg_with_roots(tmp_path, [str(a), str(b)])
    assert os.path.realpath(str(a)) in cfg.mount_roots
    assert len(cfg.mount_roots) == 2


# ---------- scoping / browse ----------

def test_browse_roots_when_no_path(tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    out = folders.browse(cfg, None)
    assert out["path"] is None and os.path.realpath(str(root)) in out["roots"]


def test_browse_lists_subdirs(tmp_path):
    root = tmp_path / "nas"; (root / "Zakoni").mkdir(parents=True); (root / "Klijenti").mkdir()
    (root / "readme.txt").write_text("x", encoding="utf-8")  # datoteka se ne prikazuje
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    out = folders.browse(cfg, str(root))
    assert out["dirs"] == ["Klijenti", "Zakoni"]  # sortirano, bez datoteke


def test_browse_outside_root_rejected(tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    outside = tmp_path / "tajno"; outside.mkdir()
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    with pytest.raises(ValueError):
        folders.browse(cfg, str(outside))
    with pytest.raises(ValueError):
        folders.browse(cfg, "/etc")


def test_browse_symlink_escape_rejected(tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    outside = tmp_path / "tajno"; outside.mkdir(); (outside / "secret").mkdir()
    os.symlink(str(outside), str(root / "link"))
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    with pytest.raises(ValueError):
        folders.browse(cfg, str(root / "link"))  # realpath vodi van korijena


def test_browse_no_roots_configured_rejects(tmp_path):
    cfg = _cfg_with_roots(tmp_path, [])
    with pytest.raises(ValueError):
        folders.browse(cfg, str(tmp_path))


# ---------- registar CRUD ----------

def test_register_and_list(spine, tmp_path):
    root = tmp_path / "nas"; (root / "Zakoni").mkdir(parents=True)
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    row = folders.register(spine, cfg, str(root / "Zakoni"), "zakoni", "Propisi", "ana")
    assert row["role"] == "zakoni" and row["label"] == "Propisi"
    assert row["path"] == os.path.realpath(str(root / "Zakoni"))
    assert [f["role"] for f in folders.list_folders(spine)] == ["zakoni"]


def test_register_outside_root_rejected(spine, tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    outside = tmp_path / "tajno"; outside.mkdir()
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    with pytest.raises(ValueError):
        folders.register(spine, cfg, str(outside), "zakoni", "", "ana")


def test_register_nonexistent_rejected(spine, tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    with pytest.raises(ValueError):
        folders.register(spine, cfg, str(root / "nema"), "zakoni", "", "ana")


def test_update_and_remove(spine, tmp_path):
    root = tmp_path / "nas"; (root / "M").mkdir(parents=True)
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    row = folders.register(spine, cfg, str(root / "M"), "ostalo", "M", "ana")
    upd = folders.update(spine, row["id"], role="klijenti", enabled=0)
    assert upd["role"] == "klijenti" and upd["enabled"] == 0
    folders.remove(spine, row["id"])
    assert folders.list_folders(spine) == []
    with pytest.raises(ValueError):
        folders.update(spine, 999, role="x")


# ---------- endpointi ----------

def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_folders_endpoints_crud(spine, tmp_path):
    root = tmp_path / "nas"; (root / "Zakoni").mkdir(parents=True)
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    c = _client(spine, cfg); tok = _token(c, spine)
    H = {"Authorization": f"Bearer {tok}"}
    # browse
    b = c.get("/folders/browse", params={"path": str(root)}, headers=H).json()
    assert b["dirs"] == ["Zakoni"]
    # register
    r = c.post("/folders", json={"path": str(root / "Zakoni"), "role": "zakoni", "label": "P"}, headers=H)
    assert r.status_code == 200
    fid = r.json()["id"]
    assert any(f["id"] == fid for f in c.get("/folders", headers=H).json())
    # update + delete
    assert c.post(f"/folders/{fid}", json={"enabled": 0}, headers=H).json()["enabled"] == 0
    assert c.delete(f"/folders/{fid}", headers=H).status_code == 200
    assert c.get("/folders", headers=H).json() == []


def test_folders_browse_outside_root_400(spine, tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    cfg = _cfg_with_roots(tmp_path, [str(root)])
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.get("/folders/browse", params={"path": "/etc"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400


def test_folders_endpoints_need_auth(spine, tmp_path):
    cfg = _cfg_with_roots(tmp_path, [str(tmp_path)])
    c = _client(spine, cfg)
    assert c.get("/folders").status_code == 401
    assert c.get("/folders/browse").status_code == 401


def test_ui_mape_authed(spine, tmp_path):
    cfg = _cfg_with_roots(tmp_path, [str(tmp_path)])
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.get("/ui/mape", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "Mrežne mape" in r.text and "/folders/browse" in r.text
    assert "propisi" in r.text  # jedna glavna mapa, podmape po vrsti
    assert "@font-face" in r.text and "innerHTML" not in r.text


def test_ui_mape_no_auth_redirects(spine, tmp_path):
    cfg = _cfg_with_roots(tmp_path, [str(tmp_path)])
    c = _client(spine, cfg)
    r = c.get("/ui/mape", follow_redirects=False)
    assert r.status_code == 303


def test_ui_postavke_lists_config_screens(spine, tmp_path):
    cfg = _cfg_with_roots(tmp_path, [str(tmp_path)])
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.get("/ui/postavke", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "Postavke" in r.text
    assert "/ui/mape" in r.text and "/ui/obveze-tipovi" in r.text


def test_ui_postavke_no_auth_redirects(spine, tmp_path):
    cfg = _cfg_with_roots(tmp_path, [str(tmp_path)])
    c = _client(spine, cfg)
    r = c.get("/ui/postavke", follow_redirects=False)
    assert r.status_code == 303


def test_dashboard_nav_has_postavke_not_mape(spine, tmp_path):
    # konfiguracija je u Postavkama, ne u glavnom ribonu
    cfg = _cfg_with_roots(tmp_path, [str(tmp_path)])
    c = _client(spine, cfg); tok = _token(c, spine)
    r = c.get("/", headers={"Authorization": f"Bearer {tok}"})
    assert '/ui/postavke"' in r.text
    assert '>Mape<' not in r.text  # nema Mape linka u ribonu


def test_skener_is_valid_role():
    from ragspine.business import folders
    assert "skener" in folders.ROLES
