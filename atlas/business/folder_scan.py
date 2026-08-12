"""Read-only listing of a mounted folder's tree: counts subfolders/documents/PDFs and PDFs
without searchable text. Nothing is changed on disk. Full OCR is part C."""
import json
import os

from atlas.business import folders
from atlas.core import optional

_DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md", ".odt", ".rtf",
            ".png", ".jpg", ".jpeg"}


def pdf_has_text(path: str):
    """True/False whether the PDF has a text layer; None if fitz is unavailable.
    Same threshold (100 characters) as ocr.has_text_layer — the dashboard count and bulk OCR
    must look at the same criterion, otherwise the button lies."""
    fitz = optional.need("fitz", "PDF tekst-detekcija")
    if fitz is None:
        return None
    try:
        doc = fitz.open(path)
        try:
            return sum(len(page.get_text()) for page in doc) >= 100
        finally:
            doc.close()
    except Exception:
        return False


def scan(spine, cfg, folder_id: int) -> dict:
    row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise ValueError("nepoznata mapa")
    base = folders._scoped(cfg, row["path"])  # scoped realpath, symlink-escape blocked
    n_subdirs = sum(1 for e in os.scandir(base) if e.is_dir())
    n_docs = n_pdf = n_pdf_no_text = 0
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in _DOC_EXT:
                n_docs += 1
            if ext == ".pdf":
                n_pdf += 1
                if pdf_has_text(os.path.join(dirpath, f)) is False:
                    n_pdf_no_text += 1
    summary = {"n_subdirs": n_subdirs, "n_docs": n_docs, "n_pdf": n_pdf,
               "n_pdf_no_text": n_pdf_no_text}
    with spine.write() as c:
        c.execute("""INSERT INTO folder_scan(folder_id, at, n_subdirs, n_docs, n_pdf, n_pdf_no_text, summary_json)
            VALUES(?,datetime('now'),?,?,?,?,?)
            ON CONFLICT(folder_id) DO UPDATE SET at=excluded.at, n_subdirs=excluded.n_subdirs,
            n_docs=excluded.n_docs, n_pdf=excluded.n_pdf, n_pdf_no_text=excluded.n_pdf_no_text,
            summary_json=excluded.summary_json""",
                  (folder_id, n_subdirs, n_docs, n_pdf, n_pdf_no_text, json.dumps(summary)))
    return {**summary, "at": "now"}


def latest(spine, folder_id: int):
    r = spine.read().execute("SELECT * FROM folder_scan WHERE folder_id=?", (folder_id,)).fetchone()
    return dict(r) if r else None
