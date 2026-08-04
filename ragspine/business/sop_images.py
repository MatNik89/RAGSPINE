# Screenshots/photos attached to a SOP (upute). Stored on disk under
# {data_dir}/sop_images, OCR'd at upload time so their content becomes
# searchable once the SOP is approved (see sop.approve_draft).
import mimetypes
import os

from ragspine.core import security
from ragspine.docs import ocr as ocr_mod

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _images_dir(cfg) -> str:
    d = os.path.join(cfg.data_dir, "sop_images")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(filename: str) -> str:
    """basename-only, extension-allowlisted — never trust a client-supplied
    path (traversal like '../../evil.png' must resolve to just 'evil.png')."""
    name = os.path.basename(filename or "").strip()
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in _ALLOWED_EXT:
        raise ValueError(f"nedozvoljen naziv/ekstenzija slike: {filename!r}")
    return name


def add_image(spine, cfg, sop_id: int, filename: str, data: bytes, caption: str = "",
              transport=None) -> dict:
    if spine.read().execute("SELECT id FROM sop_pages WHERE id=?", (sop_id,)).fetchone() is None:
        raise ValueError(f"nepoznat SOP: {sop_id}")

    safe = _safe_name(filename)
    images_dir = _images_dir(cfg)
    n = 0
    path = os.path.join(images_dir, f"{sop_id}_{n}_{safe}")
    while os.path.exists(path):
        n += 1
        path = os.path.join(images_dir, f"{sop_id}_{n}_{safe}")

    with open(path, "wb") as f:
        f.write(data)

    # ponytail: OCR failure (bad transport, malformed response, network error)
    # must never lose the already-stored image — degrade to "" and move on.
    try:
        ocr_text = ocr_mod.ocr_page(data, cfg, transport=transport)
    except Exception:
        ocr_text = ""

    with spine.write() as c:
        image_id = c.execute(
            "INSERT INTO sop_images(sop_id,filename,path,ocr_text,caption) VALUES(?,?,?,?,?)",
            (sop_id, safe, path, ocr_text, caption),
        ).lastrowid

    return {"id": image_id, "path": path, "ocr_text": ocr_text}


def list_images(spine, sop_id: int) -> list[dict]:
    rows = spine.read().execute(
        "SELECT id, filename, caption, path, ocr_text FROM sop_images WHERE sop_id=? ORDER BY id",
        (sop_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def image_bytes(spine, cfg, image_id: int):
    row = spine.read().execute("SELECT path FROM sop_images WHERE id=?", (image_id,)).fetchone()
    if row is None:
        return None
    images_dir = os.path.realpath(_images_dir(cfg))
    resolved = os.path.realpath(row["path"])
    if not security.path_under(resolved, images_dir) or not os.path.isfile(resolved):
        return None
    mime = mimetypes.guess_type(resolved)[0] or "application/octet-stream"
    with open(resolved, "rb") as f:
        return f.read(), mime
