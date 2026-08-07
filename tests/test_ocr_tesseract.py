import io

import pytest

from atlas.docs import ocr


def _png_with_text(text="PDV 25"):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    img = Image.new("RGB", (320, 90), "white")
    ImageDraw.Draw(img).text((12, 30), text, fill="black")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


def test_tesseract_reads_text(cfg):
    if not ocr.tesseract_available():
        pytest.skip("tesseract nije na PATH-u")
    out = ocr.ocr_page_tesseract(_png_with_text("PDV 25"), cfg)
    assert "PDV" in out or "25" in out


def test_tesseract_missing_binary_returns_empty(cfg, monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    assert ocr.ocr_page_tesseract(b"notapng", cfg) == ""
