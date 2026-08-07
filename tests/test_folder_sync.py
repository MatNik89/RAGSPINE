import os

from fastapi.testclient import TestClient

from atlas.business import folder_sync, folders
from atlas.config import Config, set_config
from atlas.rag import authority
from atlas.web.api import create_app
from atlas.web.deps import add_user


def _cfg(tmp_path, roots):
    old = dict(os.environ)
    os.environ.update({"ATLAS_DATA_DIR": str(tmp_path / "d"),
                       "ATLAS_MOUNT_ROOTS": ",".join(roots)})
    try:
        cfg = Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)
    set_config(cfg)
    return cfg


def _propisi_tree(root):
    """root/Propisi/{Zakoni,Pravilnici}/*.txt"""
    p = root / "Propisi"
    (p / "Zakoni").mkdir(parents=True)
    (p / "Pravilnici").mkdir(parents=True)
    (p / "Zakoni" / "zakon-o-pdv.txt").write_text(
        "Zakon o porezu na dodanu vrijednost. Članak 1. Stopa PDV-a je 25 posto.", encoding="utf-8")
    (p / "Pravilnici" / "pravilnik-pdv.txt").write_text(
        "Pravilnik o PDV-u. Detaljno o primjeni stope i oslobođenjima.", encoding="utf-8")
    return str(p)


# ---------- authority iz podmape ----------

def test_authority_short_circuits_on_known_doc_type():
    assert authority.detect_authority("bilo što", doc_type="zakon") == ("zakon", 1.0)
    assert authority.detect_authority("x", doc_type="pravilnik")[1] == 0.95
    assert authority.detect_authority("x", doc_type="uredba")[1] == 0.9


def test_subfolder_tier_mapping(tmp_path):
    base = str(tmp_path / "Propisi")
    assert folder_sync._subfolder_tier(base, base + "/Zakoni/a.pdf") == "zakon"
    assert folder_sync._subfolder_tier(base, base + "/Pravilnici/b.pdf") == "pravilnik"
    assert folder_sync._subfolder_tier(base, base + "/Uredbe/c.pdf") == "uredba"
    assert folder_sync._subfolder_tier(base, base + "/x.pdf") is None  # bez podmape


# ---------- sync ----------

def test_sync_ingests_propisi_with_authority(spine, tmp_path):
    cfg = _cfg(tmp_path, [str(tmp_path)])
    propisi = _propisi_tree(tmp_path)
    folders.register(spine, cfg, propisi, "propisi", "Propisi", "ana")
    r = folder_sync.sync_all(spine, cfg)
    assert r["ingested"] == 2
    rows = {os.path.basename(d["path"]): d for d in spine.read().execute(
        "SELECT path, doc_type FROM documents").fetchall()}
    assert rows["zakon-o-pdv.txt"]["doc_type"] == "zakon"
    assert rows["pravilnik-pdv.txt"]["doc_type"] == "pravilnik"


def test_sync_idempotent(spine, tmp_path):
    cfg = _cfg(tmp_path, [str(tmp_path)])
    propisi = _propisi_tree(tmp_path)
    folders.register(spine, cfg, propisi, "propisi", "Propisi", "ana")
    folder_sync.sync_all(spine, cfg)
    r2 = folder_sync.sync_all(spine, cfg)
    assert r2["ingested"] == 0 and r2["skipped"] == 2  # nepromijenjeno


def test_sync_changed_file_supersedes_old(spine, tmp_path):
    cfg = _cfg(tmp_path, [str(tmp_path)])
    propisi = _propisi_tree(tmp_path)
    folders.register(spine, cfg, propisi, "propisi", "Propisi", "ana")
    folder_sync.sync_all(spine, cfg)
    # izmijeni zakon (nova stopa) -> nova aktivna verzija, stara superseded
    fp = os.path.join(propisi, "Zakoni", "zakon-o-pdv.txt")
    with open(fp, "w", encoding="utf-8") as f:
        f.write("Zakon o porezu na dodanu vrijednost. Članak 1. Stopa PDV-a je 24 posto (izmjena).")
    r = folder_sync.sync_all(spine, cfg)
    assert r["ingested"] == 1 and r["superseded"] == 1
    statuses = [row["status"] for row in spine.read().execute(
        "SELECT status FROM documents WHERE path=?", (fp,)).fetchall()]
    assert sorted(statuses) == ["active", "superseded"]


def test_sync_missing_folder_skipped_not_crash(spine, tmp_path):
    cfg = _cfg(tmp_path, [str(tmp_path)])
    missing = tmp_path / "nema"; missing.mkdir()
    folders.register(spine, cfg, str(missing), "propisi", "X", "ana")
    os.rmdir(str(missing))  # mount "pao"
    r = folder_sync.sync_all(spine, cfg)
    assert r["ingested"] == 0 and any("nedostupna" in e for e in r["errors"])


def test_sync_skips_symlink_escape(spine, tmp_path):
    # datoteka-simlink unutar registrirane mape koja vodi VAN korijena se ne smije ingestati
    root = tmp_path / "nas"; propisi = root / "Propisi"; propisi.mkdir(parents=True)
    secret = tmp_path / "tajno"; secret.mkdir()
    (secret / "lozinke.txt").write_text("TAJNI PODACI izvan NAS-a", encoding="utf-8")
    os.symlink(str(secret / "lozinke.txt"), str(propisi / "procitaj-me.txt"))
    (propisi / "javno.txt").write_text("Javni propis dostupan svima.", encoding="utf-8")
    cfg = _cfg(tmp_path, [str(root)])
    folders.register(spine, cfg, str(propisi), "propisi", "P", "ana")
    r = folder_sync.sync_all(spine, cfg)
    titles = [d["title"] for d in spine.read().execute("SELECT title FROM documents").fetchall()]
    assert "javno.txt" in titles
    assert "procitaj-me.txt" not in titles  # simlink izvan korijena preskočen
    assert not any("TAJNI" in (d["title"] or "") for d in
                   spine.read().execute("SELECT title FROM documents").fetchall())


def test_sync_root_removed_from_mount_roots_is_skipped(spine, tmp_path):
    root = tmp_path / "nas"; (root / "Propisi").mkdir(parents=True)
    (root / "Propisi" / "x.txt").write_text("nešto", encoding="utf-8")
    cfg = _cfg(tmp_path, [str(root)])
    folders.register(spine, cfg, str(root / "Propisi"), "propisi", "P", "ana")
    cfg2 = _cfg(tmp_path, [str(tmp_path / "drugi")])  # root više nije dozvoljen
    (tmp_path / "drugi").mkdir()
    r = folder_sync.sync_all(spine, cfg2)
    assert r["ingested"] == 0 and any("izvan dozvoljenih" in e for e in r["errors"])


def test_sync_updates_last_synced(spine, tmp_path):
    cfg = _cfg(tmp_path, [str(tmp_path)])
    (tmp_path / "M").mkdir()
    fid = folders.register(spine, cfg, str(tmp_path / "M"), "ostalo", "M", "ana")["id"]
    folder_sync.sync_all(spine, cfg)
    row = [f for f in folders.list_folders(spine) if f["id"] == fid][0]
    assert row["last_synced"]


# ---------- endpoint + job ----------

def test_folders_sync_endpoint(spine, tmp_path):
    cfg = _cfg(tmp_path, [str(tmp_path)])
    propisi = _propisi_tree(tmp_path)
    folders.register(spine, cfg, propisi, "propisi", "Propisi", "ana")
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.post("/folders/sync", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["ingested"] == 2


def test_folders_sync_endpoint_needs_auth(spine, tmp_path):
    cfg = _cfg(tmp_path, [str(tmp_path)])
    c = TestClient(create_app(spine, cfg))
    assert c.post("/folders/sync").status_code == 401


def test_folders_sync_job_registered(spine, cfg):
    from atlas.ops import jobs
    from atlas.ops.scheduler import Scheduler
    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    assert "folders_sync" in {j.name for j in sched.jobs}
