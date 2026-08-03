from ragspine.docs import ocr


def test_uses_tesseract_when_enough(cfg, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_page_tesseract", lambda png, c: "dovoljno teksta ovdje za prag")
    called = []
    monkeypatch.setattr(ocr, "ocr_page", lambda *a, **k: called.append(1) or "VLM")
    text, engine = ocr.ocr_page_best(b"x", cfg)
    assert engine == "tesseract" and not called


def test_falls_back_to_vlm_when_tesseract_empty(cfg, monkeypatch):
    cfg.ocr_url = "https://vlm.example"
    monkeypatch.setattr(ocr, "ocr_page_tesseract", lambda png, c: "")
    monkeypatch.setattr(ocr, "ocr_page", lambda png, c, transport=None: "tekst s vlm-a")
    text, engine = ocr.ocr_page_best(b"x", cfg)
    assert engine == "vlm" and text == "tekst s vlm-a"


def test_no_engine_returns_none(cfg, monkeypatch):
    cfg.ocr_url = ""
    monkeypatch.setattr(ocr, "ocr_page_tesseract", lambda png, c: "")
    text, engine = ocr.ocr_page_best(b"x", cfg)
    assert engine == "none" and text == ""
