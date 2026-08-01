"""Unlimited-OCR client: rasterize PDF, OCR via VLM server, invisible text layer, bulk skip-if-text."""
import base64
import json
import os
import urllib.error
import urllib.request

from ragspine.core import optional
from ragspine.docs.ingest import ingest_text


class OCRUnavailable(Exception):
    pass


def _fitz():
    fitz = optional.need("fitz", "OCR/PDF")
    if fitz is None:
        raise OCRUnavailable("PyMuPDF (fitz) nije instaliran")
    return fitz


def has_text_layer(path: str, min_chars: int = 100) -> bool:
    fitz = _fitz()
    doc = fitz.open(path)
    try:
        total = sum(len(page.get_text()) for page in doc)
    finally:
        doc.close()
    return total >= min_chars


def rasterize(path: str, dpi: int = 300) -> list[bytes]:
    fitz = _fitz()
    doc = fitz.open(path)
    try:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
    finally:
        doc.close()


def _default_transport(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def ocr_page(png: bytes, cfg, transport=None) -> str:
    transport = transport or _default_transport
    b64 = base64.b64encode(png).decode()
    body = {
        "model": "unlimited-ocr",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Prepiši sav tekst s ove slike, samo tekst."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    try:
        resp = transport(f"{cfg.ocr_url}/v1/chat/completions", {"content-type": "application/json"}, body)
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, urllib.error.URLError):
        return ""


def _insert_all(page, text: str, fontsize: int = 6, min_fontsize: int = 3, chunk: int = 4000):
    """Place ALL of text into invisible stacked boxes over page.rect.

    insert_textbox() writes NOTHING when the piece doesn't fit (negative
    return = rolled back, zero chars committed) — so a single oversized call
    silently drops the whole page. Here we feed it in chunks, halving the
    chunk on overflow (never re-trying a chunk that already failed at a
    smaller size) and shrinking fontsize if even one char won't fit, so
    large OCR output always ends up fully placed instead of dropped.
    """
    rest = text
    guard = 0
    while rest and guard < 5000:
        guard += 1
        piece = rest[:chunk]
        rc = page.insert_textbox(page.rect, piece, fontsize=fontsize, render_mode=3)
        if rc >= 0:
            rest = rest[len(piece):]
            chunk = 4000
            continue
        if chunk > 1:
            chunk = max(1, chunk // 2)
            continue
        if fontsize > min_fontsize:
            fontsize -= 1
            chunk = 4000
            continue
        # a single char at the smallest fontsize still won't fit (pathological
        # rect) — drop it rather than loop forever; everything else is placed.
        rest = rest[1:]


def write_text_layer(path: str, page_texts: list[str], out_path: str | None = None) -> str:
    fitz = _fitz()
    doc = fitz.open(path)
    try:
        for page, text in zip(doc, page_texts):
            if text:
                _insert_all(page, text)
        out = out_path or f"{os.path.splitext(path)[0]}_ocr.pdf"
        doc.save(out)
        return out
    finally:
        doc.close()


def resolve_scoped_path(cfg, path: str) -> str:
    """Resolve path and require it to live inside cfg.nas_root (or cfg.data_dir
    if no NAS root configured) — blocks arbitrary file read/write via a
    client-supplied path (OCR output is written next to the input, and OCR
    text is ingested into the SHARED RAG index)."""
    root = os.path.realpath(cfg.nas_root or cfg.data_dir)
    resolved = os.path.realpath(path)
    if os.path.commonpath([resolved, root]) != root:
        raise ValueError(f"put izvan dozvoljenog direktorija: {path!r}")
    return resolved


def ocr_pdf(spine, cfg, path: str, transport=None, force: bool = False) -> dict:
    path = resolve_scoped_path(cfg, path)
    if not force and has_text_layer(path):
        return {"skipped": True, "out": path, "pages": 0}
    page_texts = [ocr_page(png, cfg, transport=transport) for png in rasterize(path)]
    out = write_text_layer(path, page_texts)
    full_text = "\n\n".join(t for t in page_texts if t)
    if not full_text.strip():
        return {"skipped": False, "pages": len(page_texts), "out": out, "ocr_empty": True}
    ingest_text(spine, full_text, title=os.path.basename(path), path=path)
    return {"skipped": False, "pages": len(page_texts), "out": out}


def bulk_ocr(spine, cfg, folder: str, transport=None) -> dict:
    result = {"processed": 0, "skipped": 0, "errors": []}
    for root, _, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            fpath = os.path.join(root, fname)
            try:
                res = ocr_pdf(spine, cfg, fpath, transport=transport)
            except Exception as e:
                result["errors"].append(f"{fpath}: {e}")
                continue
            result["skipped" if res["skipped"] else "processed"] += 1
    return result
