import os
from atlas.ops import backup
from atlas.core.spine import init_spine
from tests.conftest import complete_setup


def _seed(cfg):
    s = init_spine(cfg.db_path)
    with s.write() as c:
        c.execute("INSERT INTO users(username, pw_hash, role) VALUES('ana','x','owner')")
    return s


def test_create_and_verify(cfg):
    _seed(cfg)
    b = backup.create_backup(cfg, stamp="20260805-000001")
    assert b["name"] == "atlas-20260805-000001.db"
    assert os.path.exists(b["path"]) and b["size"] > 0
    if os.name != "nt":
        import stat
        assert stat.S_IMODE(os.stat(b["path"]).st_mode) == 0o600
    assert backup.verify_backup(b["path"]) is True
    # snapshot sadrži podatke
    import sqlite3
    con = sqlite3.connect(b["path"])
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    con.close()


def test_list_and_prune(cfg):
    _seed(cfg)
    for i in range(5):
        backup.create_backup(cfg, stamp=f"20260805-00000{i}")
    lst = backup.list_backups(cfg)
    assert len(lst) == 5
    assert lst[0]["name"] > lst[-1]["name"]  # najnoviji prvi
    removed = backup.prune(cfg, keep=2)
    assert removed == 3
    assert len(backup.list_backups(cfg)) == 2


def test_resolve_backup_blocks_traversal(cfg):
    _seed(cfg)
    b = backup.create_backup(cfg, stamp="20260805-000009")
    assert backup.resolve_backup(cfg, b["name"]).endswith(b["name"])
    import pytest
    with pytest.raises(ValueError):
        backup.resolve_backup(cfg, "../../etc/passwd")
    with pytest.raises(ValueError):
        backup.resolve_backup(cfg, "nepostoji.db")


def _count_users(db):
    import sqlite3
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        con.close()


def test_restore_replaces_db(cfg):
    # restore je CLI-operacija dok je server ZAUSTAVLJEN — test koristi zatvorene
    # konekcije (nema živog Spinea koji drži lock)
    import sqlite3
    from atlas.core.spine import SCHEMA
    con = sqlite3.connect(cfg.db_path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO users(username, pw_hash, role) VALUES('ana','x','owner')")
    con.commit()
    con.close()
    b = backup.create_backup(cfg, stamp="20260805-000010")
    con = sqlite3.connect(cfg.db_path)
    con.execute("INSERT INTO users(username, pw_hash, role) VALUES('boris','x','member')")
    con.commit()
    con.close()
    assert _count_users(cfg.db_path) == 2
    res = backup.restore_backup(cfg, b["path"])
    assert res["restored_from"] == b["name"]
    assert os.path.exists(cfg.db_path + ".prerestore")
    assert _count_users(cfg.db_path) == 1  # vraćeno na snapshot


def test_restore_rejects_bad_backup(cfg, tmp_path):
    _seed(cfg)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite db")
    import pytest
    with pytest.raises(ValueError):
        backup.restore_backup(cfg, str(bad))


def _admin(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    # app I backup moraju dijeliti istu bazu (u produkciji spine = cfg.db_path)
    s = init_spine(cfg.db_path)
    c = TestClient(create_app(s, cfg))
    add_user(s, "ana", "pw")
    complete_setup(s)
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_backup_routes_admin(spine, cfg):
    c, h = _admin(spine, cfg)
    # napravi
    r = c.post("/backup", headers=h)
    assert r.status_code == 200 and r.json()["size"] > 0
    name = r.json()["name"]
    # popis
    lst = c.get("/backup/list", headers=h).json()
    assert any(x["name"] == name for x in lst)
    # download
    dl = c.get(f"/backup/download/{name}", headers=h)
    assert dl.status_code == 200 and len(dl.content) > 0
    # traversal blokiran
    assert c.get("/backup/download/..%2F..%2Fetc%2Fpasswd", headers=h).status_code == 404
    # UI
    assert c.get("/ui/backup", headers=h).status_code == 200


def test_backup_routes_worker_forbidden(spine, cfg):
    c, h = _admin(spine, cfg)
    from atlas.web.deps import add_user
    add_user(init_spine(cfg.db_path), "boris", "pw", "radnik")  # ista baza kao app
    wtok = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    wh = {"Authorization": f"Bearer {wtok}"}
    assert c.post("/backup", headers=wh).status_code == 403
    assert c.get("/backup/list", headers=wh).status_code == 403


def test_restore_rejects_non_atlas_sqlite(cfg, tmp_path):
    _seed(cfg)
    import sqlite3, pytest
    valid = tmp_path / "other.db"
    con = sqlite3.connect(str(valid)); con.execute("CREATE TABLE t(x)"); con.commit(); con.close()
    with pytest.raises(ValueError):  # valjan sqlite ali nema ATLAS shemu
        backup.restore_backup(cfg, str(valid))


def test_restore_rejects_nonexistent_path(cfg):
    _seed(cfg)
    import pytest
    with pytest.raises(ValueError):  # tipfeler putanje ne smije zamijeniti bazu praznom
        backup.restore_backup(cfg, "/tmp/ne-postoji-12345.db")


def test_prune_keep_zero_keeps_at_least_one(cfg):
    _seed(cfg)
    for i in range(3):
        backup.create_backup(cfg, stamp=f"20260805-11000{i}")
    backup.prune(cfg, keep=0)  # ne smije obrisati sve
    assert len(backup.list_backups(cfg)) >= 1
