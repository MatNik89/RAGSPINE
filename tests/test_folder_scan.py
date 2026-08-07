import os

from atlas.business import folder_scan, folders
from atlas.config import Config


def _cfg_roots(tmp_path, roots):
    old = dict(os.environ)
    os.environ.update({"ATLAS_DATA_DIR": str(tmp_path / "data"),
                       "ATLAS_MOUNT_ROOTS": ",".join(roots)})
    try:
        return Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)


def _mk_klijenti(tmp_path):
    root = tmp_path / "share"
    kl = root / "KLIJENTI"
    (kl / "PERIĆ PERO" / "2024").mkdir(parents=True)
    (kl / "PERIĆ PERO" / "2024" / "doh.txt").write_text("x", encoding="utf-8")
    (kl / "PODUZEĆE X D.O.O.").mkdir(parents=True)
    (kl / "PODUZEĆE X D.O.O." / "ugovor.pdf").write_bytes(b"%PDF-1.4 nije pravi")
    return root, kl


def test_scan_counts(spine, tmp_path):
    root, kl = _mk_klijenti(tmp_path)
    cfg = _cfg_roots(tmp_path, [str(root)])
    fid = folders.register(spine, cfg, str(kl), "klijenti")["id"]
    res = folder_scan.scan(spine, cfg, fid)
    assert res["n_subdirs"] == 2          # dvije klijentske mape (prva razina)
    assert res["n_docs"] >= 2             # doh.txt + ugovor.pdf
    assert res["n_pdf"] == 1
    assert folder_scan.latest(spine, fid)["n_subdirs"] == 2


def test_scan_unknown_folder(spine, tmp_path):
    cfg = _cfg_roots(tmp_path, [str(tmp_path)])
    import pytest
    with pytest.raises(ValueError):
        folder_scan.scan(spine, cfg, 999)
