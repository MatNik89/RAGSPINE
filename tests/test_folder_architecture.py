import os

from ragspine.business import folder_architecture as fa
from ragspine.business import onboarding


def test_propose_reports_missing_without_touching_disk(spine, cfg):
    prop = fa.propose(spine, cfg)
    assert [e["name"] for e in prop["must_have"]] == list(fa.MUST_HAVE)
    assert all(e["exists"] is False for e in prop["must_have"])
    assert prop["n_missing"] == len(fa.MUST_HAVE)
    # preview ne dira disk
    assert not os.path.isdir(os.path.join(prop["root"], "KLIJENTI"))


def test_apply_creates_missing_and_is_idempotent(spine, cfg):
    res = fa.apply(spine, cfg)
    assert res["n_created"] == len(fa.MUST_HAVE)
    for name in fa.MUST_HAVE:
        assert os.path.isdir(os.path.join(fa._root(cfg), name))
    assert fa.apply(spine, cfg)["n_created"] == 0
    assert fa.propose(spine, cfg)["n_missing"] == 0


def test_client_subdirs_proposed_and_created(spine, cfg):
    client = onboarding.create_client(spine, cfg, {"name": "Pekara Mlinar"}, owner="ana")
    prop = fa.propose(spine, cfg)
    mine = [c for c in prop["clients"] if c["client_id"] == client["id"]]
    assert mine and [e["name"] for e in mine[0]["subdirs"]] == list(fa.CLIENT_SUBDIRS)
    assert all(not e["exists"] for e in mine[0]["subdirs"])
    fa.apply(spine, cfg)
    for s in fa.CLIENT_SUBDIRS:
        assert os.path.isdir(os.path.join(client["folder_path"], s))


def test_evil_nas_folder_skipped_fail_closed(spine, cfg, tmp_path):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, nas_folder) VALUES('Zli', '../../izvan')")
    prop = fa.propose(spine, cfg)
    assert all(c["name"] != "Zli" for c in prop["clients"])
    fa.apply(spine, cfg)
    assert not os.path.isdir(os.path.join(os.path.dirname(fa._root(cfg)), "izvan"))


def test_api_preview_and_apply(spine, cfg):
    from fastapi.testclient import TestClient
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user
    add_user(spine, "ana", "tajna")
    c = TestClient(create_app(spine, cfg))
    assert c.get("/folder-architecture").status_code in (401, 403)  # prije logina (cookie!)
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    prop = c.get("/folder-architecture", headers=h).json()
    assert prop["n_missing"] > 0
    res = c.post("/folder-architecture/apply", headers=h).json()
    assert res["n_created"] == prop["n_missing"]
    assert c.get("/folder-architecture", headers=h).json()["n_missing"] == 0
    r = c.get("/ui/arhitektura", headers=h)
    assert r.status_code == 200 and "Arhitektura mapa" in r.text


def test_preview_filtered_and_apply_admin_only(spine, cfg):
    from fastapi.testclient import TestClient
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user

    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    owner = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    add_user(spine, "boris", "pw")
    worker = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    ho = {"Authorization": f"Bearer {owner}"}
    hw = {"Authorization": f"Bearer {worker}"}

    a = onboarding.create_client(spine, cfg, {"name": "Alfa"}, owner="gazda")
    onboarding.create_client(spine, cfg, {"name": "Beta"}, owner="gazda")
    bid = spine.read().execute("SELECT id FROM users WHERE username='boris'").fetchone()["id"]
    r = c.post(f"/workers/{bid}/visibility",
               json={"sees_all": False, "client_ids": [a["id"]]}, headers=ho)
    assert r.status_code == 200

    # restringirani radnik: samo Alfa u preview-u, n_missing preračunat
    prop = c.get("/folder-architecture", headers=hw).json()
    assert [x["name"] for x in prop["clients"]] == ["Alfa"]
    # apply nije za članove — samo admin/owner
    assert c.post("/folder-architecture/apply", headers=hw).status_code == 403
    assert c.post("/folder-architecture/apply", headers=ho).status_code == 200
