import os

import pytest

from atlas.config import Config
from atlas.docs import ocr


def _cfg(tmp_path, share):
    old = dict(os.environ)
    os.environ.update({"ATLAS_DATA_DIR": str(tmp_path / "d"),
                       "ATLAS_MOUNT_ROOTS": str(share)})
    try:
        return Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)


def test_audit_counts_no_text(spine, tmp_path):
    fitz = pytest.importorskip("fitz")
    share = tmp_path / "share"; share.mkdir()
    d = fitz.open(); d.new_page(); d.save(str(share / "skan.pdf")); d.close()  # bez teksta
    d = fitz.open(); p = d.new_page()
    p.insert_text((72, 72), "ima teksta ovdje puno " * 8); d.save(str(share / "txt.pdf")); d.close()
    cfg = _cfg(tmp_path, share)
    a = ocr.audit_folder(cfg, str(share))
    assert a["n_pdf"] == 2 and a["n_pdf_no_text"] == 1


def test_bulk_ocr_processes(spine, tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    share = tmp_path / "share"; share.mkdir()
    d = fitz.open(); d.new_page(); d.save(str(share / "skan.pdf")); d.close()
    cfg = _cfg(tmp_path, share)
    long_text = "tekst dovoljne duljine za test. " * 6
    monkeypatch.setattr(ocr, "ocr_page_best", lambda png, c, transport=None: (long_text, "tesseract"))
    res = ocr.bulk_ocr(spine, cfg, str(share))
    assert res["processed"] == 1 and res["engines"].get("tesseract", 0) >= 1
