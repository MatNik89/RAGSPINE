import os

import pytest

from ragspine.config import Config
from ragspine.docs import ocr


def _cfg(tmp_path, share):
    old = dict(os.environ)
    os.environ.update({"RAGSPINE_DATA_DIR": str(tmp_path / "d"),
                       "RAGSPINE_MOUNT_ROOTS": str(share)})
    try:
        return Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)


def test_ocr_pdf_writes_text_layer_in_place(spine, tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    share = tmp_path / "share"; share.mkdir()
    p = str(share / "skan.pdf")
    doc = fitz.open(); doc.new_page(); doc.save(p); doc.close()  # PDF bez teksta
    cfg = _cfg(tmp_path, share)
    long_text = "Ovo je OCR tekst za test. " * 8  # >100 znakova (prag has_text_layer)
    monkeypatch.setattr(ocr, "ocr_page_best",
                        lambda png, c, transport=None: (long_text, "tesseract"))
    res = ocr.ocr_pdf(spine, cfg, p)
    assert res["out"] == os.path.realpath(p) and not res.get("skipped")
    assert res["engines"].get("tesseract", 0) >= 1
    assert ocr.has_text_layer(p)  # isti PDF sad ima tekst
