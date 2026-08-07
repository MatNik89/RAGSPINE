import pytest
from fastapi.testclient import TestClient

from atlas.core import optional
from atlas.docs import ocr
from atlas.web.api import create_app
from atlas.web.deps import add_user

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
def test_write_text_layer_large_text_not_dropped_on_overflow(tmp_path):
    # insert_textbox() writes NOTHING when a piece overflows the box — a naive
    # single-shot insert of a big OCR page silently produces an empty layer.
    p = tmp_path / "blank.pdf"
    _make_blank_pdf(str(p))
    # Kept ASCII-only deliberately — this test isolates overflow handling from
    # encoding; diacritic fidelity is covered by test_write_text_layer_diacritics.
    big_text = ("Racun broj " * 2800) + "ZADNJI-REDAK-30000"  # ~30KB, ends distinctively
    assert len(big_text) > 30000

    out = ocr.write_text_layer(str(p), [big_text])

    doc = fitz.open(out)
    try:
        extracted = doc[0].get_text()
    finally:
        doc.close()
    assert "ZADNJI-REDAK-30000" in extracted, "text from the END of the page was dropped on overflow"
    assert "Racun broj" in extracted


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
@pytest.mark.skipif(not ocr._find_unicode_font(), reason="no Unicode TTF found on this host")
def test_write_text_layer_diacritics(tmp_path):
    # base-14 helv is WinAnsi-encoded and has no glyphs for č/ć/š/ž/đ — without
    # a Unicode TTF this would extract back as "?????", breaking the whole
    # point of a searchable text layer for Croatian OCR output.
    p = tmp_path / "blank.pdf"
    _make_blank_pdf(str(p))

    out = ocr.write_text_layer(str(p), ["Račun čćšžđ ČĆŠŽĐ"])

    doc = fitz.open(out)
    try:
        extracted = doc[0].get_text()
    finally:
        doc.close()
    assert "Račun" in extracted
    assert "čćšžđ" in extracted
    assert "ČĆŠŽĐ" in extracted
    assert "?????" not in extracted


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_pdf_all_empty_ocr_skips_ingest(spine, cfg, tmp_path):
    p = tmp_path / "scan.pdf"
    _make_blank_pdf(str(p))

    result = ocr.ocr_pdf(spine, cfg, str(p), transport=lambda u, h, b: {"unexpected": "shape"})

    assert result["skipped"] is False
    assert result.get("ocr_empty") is True
    row = spine.read().execute("SELECT * FROM documents WHERE path=?", (str(p),)).fetchone()
    assert row is None


def _auth_headers(spine, cfg):
    add_user(spine, "ana", "tajna")
    c = TestClient(create_app(spine, cfg))
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_endpoint_rejects_path_outside_root(spine, cfg):
    c, headers = _auth_headers(spine, cfg)
    r = c.post("/ocr", json={"path": "/etc/hosts"}, headers=headers)
    assert r.status_code == 400
    row = spine.read().execute("SELECT * FROM documents").fetchone()
    assert row is None


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_endpoint_allows_path_inside_root(spine, cfg, tmp_path):
    # cfg fixture points data_dir at tmp_path, so a file written under it is in-root.
    p = tmp_path / "text.pdf"
    _make_text_pdf(str(p))
    c, headers = _auth_headers(spine, cfg)
    r = c.post("/ocr", json={"path": str(p)}, headers=headers)
    assert r.status_code == 200
    assert r.json()["skipped"] is True


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_pdf_ocrs_blank_pdf_and_ingests(spine, cfg, tmp_path):
    p = tmp_path / "scan.pdf"
    _make_blank_pdf(str(p))
    cfg.ocr_url = "http://fake/ocr"  # dispatcher zove VLM fallback samo ako je ocr_url zadan

    result = ocr.ocr_pdf(spine, cfg, str(p), transport=lambda u, h, b: {
        "choices": [{"message": {"content": "OCR tekst"}}]
    })

    assert result["skipped"] is False
    assert result["pages"] == 1
    assert result["out"] == str(p)

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

    assert result == {"skipped": True, "out": str(p), "pages": 0, "engines": {}}


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_pdf_skips_signed_pdf_untouched(spine, cfg, tmp_path, monkeypatch):
    p = tmp_path / "signed.pdf"
    _make_blank_pdf(str(p))
    before = p.read_bytes()
    monkeypatch.setattr(ocr, "pdf_is_signed", lambda path: True)
    result = ocr.ocr_pdf(spine, cfg, str(p), transport=lambda u, h, b: (_ for _ in ()).throw(
        AssertionError("potpisani PDF se ne smije OCR-ati")))
    assert result["skipped"] is True and result["reason"] == "signed"
    assert p.read_bytes() == before


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_ocr_pdf_empty_result_does_not_rewrite(spine, cfg, tmp_path):
    p = tmp_path / "blank2.pdf"
    _make_blank_pdf(str(p))
    before = p.read_bytes()
    # bez ocr_url i bez tesseract teksta -> "" -> original mora ostati netaknut
    result = ocr.ocr_pdf(spine, cfg, str(p))
    assert result["ocr_empty"] is True
    assert p.read_bytes() == before
