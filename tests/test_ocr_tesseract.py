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


def test_tesseract_available_koristi_winpath_find_binary(monkeypatch):
    """Preflight nalazi tesseract i izvan PATH-a (winpath.find_binary zna
    poznate instalacijske lokacije) — runtime OCR mora koristiti istu
    provjeru, ne goli shutil.which, inače je preflight zelen a OCR tiho
    mrtav."""
    monkeypatch.setattr(ocr.winpath, "find_binary",
                        lambda k: r"C:\Program Files\Tesseract-OCR\tesseract.exe" if k == "tesseract" else None)
    assert ocr.tesseract_available() is True
    monkeypatch.setattr(ocr.winpath, "find_binary", lambda k: None)
    assert ocr.tesseract_available() is False


def test_ocr_page_tesseract_koristi_puni_put_iz_find_binary(cfg, monkeypatch):
    exe = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    monkeypatch.setattr(ocr.winpath, "find_binary", lambda k: exe)
    seen = {}

    def _fake_run_isolated(cmd, timeout=120):
        seen["cmd"] = cmd
        return 0, "PDV 25", ""

    monkeypatch.setattr(ocr, "run_isolated", _fake_run_isolated)
    out = ocr.ocr_page_tesseract(b"notapng", cfg)
    assert out == "PDV 25"
    assert seen["cmd"][0] == exe
