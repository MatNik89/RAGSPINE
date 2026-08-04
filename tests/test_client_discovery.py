import os

from ragspine.business import client_discovery, folders
from ragspine.config import Config


def _cfg_roots(tmp_path, roots):
    old = dict(os.environ)
    os.environ.update({"RAGSPINE_DATA_DIR": str(tmp_path / "data"),
                       "RAGSPINE_MOUNT_ROOTS": ",".join(roots)})
    try:
        return Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)


def _mk(tmp_path):
    root = tmp_path / "share"; kl = root / "KLIJENTI"
    (kl / "PERIĆ PERO").mkdir(parents=True)
    (kl / "PODUZEĆE X D.O.O").mkdir(parents=True)
    return root, kl


def test_discover_and_commit(spine, tmp_path):
    root, kl = _mk(tmp_path)
    cfg = _cfg_roots(tmp_path, [str(root)])
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    cand = {c["raw_name"]: c for c in client_discovery.discover(spine, cfg, fid)}
    assert cand["PERIĆ PERO"]["guessed_type"] == "person"
    assert cand["PODUZEĆE X D.O.O"]["guessed_type"] == "company"
    res = client_discovery.commit(spine, cfg, fid, [
        {"subdir": "PERIĆ PERO", "name": "Perić Pero", "action": "import"},
        {"subdir": "PODUZEĆE X D.O.O", "name": "Poduzeće X d.o.o.", "action": "skip"},
    ])
    assert res["created"] == 1 and res["skipped"] == 1
    names = [r["name"] for r in spine.read().execute("SELECT name FROM clients").fetchall()]
    assert names == ["Perić Pero"]


def test_discover_flags_existing_match(spine, tmp_path):
    root, kl = _mk(tmp_path)
    cfg = _cfg_roots(tmp_path, [str(root)])
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name) VALUES('Perić Pero')").lastrowid
    match = {c["raw_name"]: c["match_id"] for c in client_discovery.discover(spine, cfg, fid)}
    assert match["PERIĆ PERO"] == cid  # 'PERIĆ PERO' ~ 'Perić Pero' (fold+sort)


def test_commit_idempotent(spine, tmp_path):
    root, kl = _mk(tmp_path)
    cfg = _cfg_roots(tmp_path, [str(root)])
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    item = [{"subdir": "PERIĆ PERO", "name": "Perić Pero", "action": "import"}]
    client_discovery.commit(spine, cfg, fid, item)
    client_discovery.commit(spine, cfg, fid, item)
    n = spine.read().execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
    assert n == 1  # bez duplikata (isti nas_folder)


def test_merge_into_existing(spine, tmp_path):
    root, kl = _mk(tmp_path)
    cfg = _cfg_roots(tmp_path, [str(root)])
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name) VALUES('Perić Pero')").lastrowid
    res = client_discovery.commit(spine, cfg, fid, [
        {"subdir": "PERIĆ PERO", "name": "Perić Pero", "action": "merge", "merge_id": cid}])
    assert res["merged"] == 1
    row = spine.read().execute("SELECT nas_folder FROM clients WHERE id=?", (cid,)).fetchone()
    assert row["nas_folder"].endswith("PERIĆ PERO")
