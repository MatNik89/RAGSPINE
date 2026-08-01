import pytest

from ragspine.core import optional
from ragspine.docs import ocr

fitz = optional.need("fitz", "OCR/PDF")


def fake_transport_ok(url, headers, body):
    return {"choices": [{"message": {"content": "Račun 55"}}]}


def fake_transport_malformed(url, headers, body):
    return {"unexpected": "shape"}


def test_ocr_page_returns_content(cfg):
    assert ocr.ocr_page(b"fakepng", cfg, transport=fake_transport_ok) == "Račun 55"


def test_ocr_page_malformed_returns_empty(cfg):
    assert ocr.ocr_page(b"fakepng", cfg, transport=fake_transport_malformed) == ""


def _make_text_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    # repeated to clear has_text_layer's default 100-char threshold
    for i in range(10):
        page.insert_text((72, 72 + i * 14), "Originalni tekst")
    doc.save(path)
    doc.close()


def _make_blank_pdf(path):
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_has_text_layer_true_for_text_pdf(tmp_path):
    p = tmp_path / "text.pdf"
    _make_text_pdf(str(p))
    assert ocr.has_text_layer(str(p), min_chars=5) is True


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_has_text_layer_false_for_blank_pdf(tmp_path):
    p = tmp_path / "blank.pdf"
    _make_blank_pdf(str(p))
    assert ocr.has_text_layer(str(p)) is False


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_rasterize_returns_png_bytes_per_page(tmp_path):
    p = tmp_path / "blank.pdf"
    _make_blank_pdf(str(p))
    pages = ocr.rasterize(str(p), dpi=72)
    assert len(pages) == 1
    assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_write_text_layer_invisible_and_searchable(tmp_path):
    p = tmp_path / "blank.pdf"
    _make_blank_pdf(str(p))
    out = ocr.write_text_layer(str(p), ["OCR tekst"])
    assert out == str(tmp_path / "blank_ocr.pdf")
    doc = fitz.open(out)
    try:
        assert "OCR tekst" in doc[0].get_text()
    finally:
        doc.close()


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_pdf_ocrs_blank_pdf_and_ingests(spine, cfg, tmp_path):
    p = tmp_path / "scan.pdf"
    _make_blank_pdf(str(p))

    result = ocr.ocr_pdf(spine, cfg, str(p), transport=lambda u, h, b: {
        "choices": [{"message": {"content": "OCR tekst"}}]
    })

    assert result["skipped"] is False
    assert result["pages"] == 1
    assert result["out"] == str(tmp_path / "scan_ocr.pdf")

    doc = fitz.open(result["out"])
    try:
        assert "OCR tekst" in doc[0].get_text()
    finally:
        doc.close()

    row = spine.read().execute("SELECT * FROM documents WHERE title=?", ("scan.pdf",)).fetchone()
    assert row is not None


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_pdf_skips_pdf_with_text_layer(spine, cfg, tmp_path):
    p = tmp_path / "text.pdf"
    _make_text_pdf(str(p))

    def blow_up(u, h, b):
        raise AssertionError("transport should not be called when skipping")

    result = ocr.ocr_pdf(spine, cfg, str(p), transport=blow_up)

    assert result == {"skipped": True, "out": str(p), "pages": 0}
