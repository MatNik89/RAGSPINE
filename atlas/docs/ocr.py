"""OCR: rasterize PDF, OCR via tesseract (lokalno) ili VLM server, invisible text
layer, bulk skip-if-text."""
import base64
import json
import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request

from atlas.core import optional, security
from atlas.core.subproc import run_isolated
from atlas.docs.ingest import ingest_text

_log = logging.getLogger(__name__)


class OCRUnavailable(Exception):
    pass


# ponytail: probed system paths, first hit wins — no fontconfig dependency, no
# cfg knob yet. Upgrade path: add cfg.ocr_font override once someone needs a
# non-default font/host.
_UNICODE_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows (%WINDIR% se ekspandira pri probi)
    "%WINDIR%\\Fonts\\arial.ttf",
    "%WINDIR%\\Fonts\\calibri.ttf",
]
_unicode_font_path: str | None = None  # None = not probed yet, "" = probed, none found
_warned_no_unicode_font = False


def _find_unicode_font() -> str:
    """First existing Unicode TTF from _UNICODE_FONT_CANDIDATES, probed once per
    process. Base-14 PDF fonts (helv) use WinAnsi encoding and have no glyphs
    for č/ć/š/ž/đ — without a Unicode TTF the OCR text layer would extract as
    '?????' for Croatian text."""
    global _unicode_font_path
    if _unicode_font_path is None:
        _unicode_font_path = next(
            (q for q in (os.path.expandvars(p) for p in _UNICODE_FONT_CANDIDATES)
             if os.path.exists(q)), "")
    return _unicode_font_path


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


def pdf_is_signed(path: str) -> bool:
    """True ako PDF ima digitalni potpis (sigflags > 0). In-place OCR bi ga
    poništio — potpisani dokumenti (e-računi) se preskaču."""
    fitz = _fitz()
    try:
        doc = fitz.open(path)
    except Exception:
        return False
    try:
        return doc.get_sigflags() > 0
    except Exception:
        return False
    finally:
        doc.close()


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


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_page_tesseract(png: bytes, cfg) -> str:
    """Lokalni OCR jedne stranice tesseractom (jezik iz cfg.ocr_langs). Nikad ne
    baca — vrati "" na grešci/nedostupno."""
    if not tesseract_available():
        return ""
    langs = getattr(cfg, "ocr_langs", "hrv+eng") or "hrv+eng"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png)
            tmp = f.name
        rc, out, _err = run_isolated(["tesseract", tmp, "stdout", "-l", langs], timeout=120)
        return out if rc == 0 else ""
    except Exception:
        return ""
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


_MIN_OK_CHARS = 20


def ocr_page_best(png: bytes, cfg, transport=None):
    """Vrati (tekst, motor). tesseract prvo; ako je prekratak i ocr_url je zadan,
    probaj VLM i uzmi dulji. motor ∈ 'tesseract'|'vlm'|'none'."""
    t = ocr_page_tesseract(png, cfg)
    if len(t.strip()) >= _MIN_OK_CHARS:
        return t, "tesseract"
    if getattr(cfg, "ocr_url", ""):
        v = ocr_page(png, cfg, transport=transport)
        if len(v.strip()) > len(t.strip()):
            return v, "vlm"
    if t.strip():
        return t, "tesseract"
    return "", "none"


def _insert_all(page, text: str, fontsize: int = 6, min_fontsize: int = 3, chunk: int = 4000,
                 fontname: str = "helv", fontfile: str | None = None):
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
        rc = page.insert_textbox(page.rect, piece, fontsize=fontsize, render_mode=3,
                                  fontname=fontname, fontfile=fontfile)
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
    font_path = _find_unicode_font()
    if font_path:
        fontname, fontfile = "atlasU", font_path
    else:
        global _warned_no_unicode_font
        if not _warned_no_unicode_font:
            # ponytail: degrade, don't crash — a missing font shouldn't block OCR.
            # Upgrade path: install one of _UNICODE_FONT_CANDIDATES on the host.
            _log.warning(
                "no Unicode TTF found (checked %s) — OCR text layer diacritics "
                "(č/ć/š/ž/đ) will render as '?' in the on-disk searchable PDF; "
                "the RAG index text is unaffected.", _UNICODE_FONT_CANDIDATES)
            _warned_no_unicode_font = True
        fontname, fontfile = "helv", None
    doc = fitz.open(path)
    try:
        for page, text in zip(doc, page_texts):
            if text:
                _insert_all(page, text, fontname=fontname, fontfile=fontfile)
        out = out_path or f"{os.path.splitext(path)[0]}_ocr.pdf"
        if os.path.realpath(out) == os.path.realpath(path):
            # fitz ne da save preko otvorenog originala — save u temp pa atomično zamijeni.
            # pid u imenu: dva konkurentna OCR-a istog PDF-a si ne gaze temp.
            tmp = f"{out}.{os.getpid()}.ocrtmp"
            try:
                doc.save(tmp)
                doc.close()
                try:
                    # zadrži mode originala — doc.save stvara fajl s default umaskom
                    os.chmod(tmp, os.stat(out).st_mode & 0o7777)
                except OSError:
                    pass
                os.replace(tmp, out)
            finally:
                try:
                    os.unlink(tmp)  # ostatak samo ako je save/replace pukao
                except OSError:
                    pass
        else:
            doc.save(out)
            doc.close()
        return out
    finally:
        if not doc.is_closed:
            doc.close()


def resolve_scoped_path(cfg, path: str) -> str:
    """Resolve path and require it to live inside a mount_root (spojene mape),
    nas_root ili data_dir — blocks arbitrary file read/write via a client-supplied
    path (OCR piše u isti PDF, a tekst ide u DIJELJENI RAG indeks)."""
    roots = [os.path.realpath(r) for r in (cfg.mount_roots or [])]
    roots.append(os.path.realpath(cfg.nas_root or cfg.data_dir))
    resolved = os.path.realpath(path)
    for root in roots:
        if root and security.path_under(resolved, root):
            return resolved
    raise ValueError(f"put izvan dozvoljenih korijena: {path!r}")


def ocr_pdf(spine, cfg, path: str, transport=None, force: bool = False) -> dict:
    path = resolve_scoped_path(cfg, path)
    if not force and has_text_layer(path):
        return {"skipped": True, "out": path, "pages": 0, "engines": {}}
    if pdf_is_signed(path):
        return {"skipped": True, "reason": "signed", "out": path, "pages": 0, "engines": {}}
    pairs = [ocr_page_best(png, cfg, transport=transport) for png in rasterize(path)]
    page_texts = [t for t, _e in pairs]
    engines: dict[str, int] = {}
    for _t, e in pairs:
        engines[e] = engines.get(e, 0) + 1
    full_text = "\n\n".join(t for t in page_texts if t)
    if not full_text.strip():
        # ništa pročitano — NE prepisuj original (prazan sloj = besmislen rewrite)
        return {"skipped": False, "pages": len(page_texts), "out": path,
                "ocr_empty": True, "engines": engines}
    # indeks PRIJE prepisivanja PDF-a: padne li ingest, original ostaje netaknut
    # i retry radi; padne li upis sloja, dokument je već u indeksu (sha-dedup
    # čini ponovni ingest no-opom).
    ingest_text(spine, full_text, title=os.path.basename(path), path=path)
    write_text_layer(path, page_texts, out_path=path)  # pretraživi sloj U ISTI PDF
    return {"skipped": False, "pages": len(page_texts), "out": path, "engines": engines}


def _walk_pdfs(base: str):
    """PDF-ovi ispod base; simlink koji resolvea izvan base se preskače
    (audit ne smije čitati, a bulk ne smije pisati izvan tražene mape)."""
    for root, _d, files in os.walk(base):
        for f in files:
            if not f.lower().endswith(".pdf"):
                continue
            fp = os.path.join(root, f)
            if os.path.islink(fp) or not security.path_under(os.path.realpath(fp), base):
                continue
            yield fp


def audit_folder(cfg, base: str) -> dict:
    """Read-only: koliko PDF-ova u mapi treba OCR (nemaju tekstualni sloj)."""
    base = resolve_scoped_path(cfg, base)
    n_pdf = n_no = 0
    sample = []
    for fp in _walk_pdfs(base):
        n_pdf += 1
        try:
            if not has_text_layer(fp):
                n_no += 1
                if len(sample) < 20:
                    sample.append(fp)
        except Exception:
            pass
    return {"n_pdf": n_pdf, "n_pdf_no_text": n_no, "sample": sample}


def bulk_ocr(spine, cfg, folder: str, transport=None) -> dict:
    folder = resolve_scoped_path(cfg, folder)
    result = {"processed": 0, "skipped": 0, "signed": 0, "ocr_empty": 0,
              "engines": {}, "errors": []}
    for fpath in _walk_pdfs(folder):
        try:
            res = ocr_pdf(spine, cfg, fpath, transport=transport)
        except Exception as e:
            result["errors"].append(f"{fpath}: {e}")
            continue
        if res["skipped"]:
            result["signed" if res.get("reason") == "signed" else "skipped"] += 1
        elif res.get("ocr_empty"):
            result["ocr_empty"] += 1  # ništa pročitano — nije "obrađen"
        else:
            result["processed"] += 1
        for eng, n in (res.get("engines") or {}).items():
            result["engines"][eng] = result["engines"].get(eng, 0) + n
    return result
