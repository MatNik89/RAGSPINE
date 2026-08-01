# Client onboarding: add a client, create its NAS folder, upload + ingest
# its documents. Backend for the "Dodaj novog klijenta" UI (built later).
import logging
import os
import re
import sqlite3

from ragspine.core import security
from ragspine.docs.ingest import ingest_file

_log = logging.getLogger(__name__)

_EXT_ALLOW = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx", ".txt", ".md"}


def _slug(name: str) -> str:
    """ASCII-only, separator-free folder segment. Any run of non-alnum chars
    (including '/', '.', '..') collapses to a single '-', so path traversal
    sequences can't survive into a folder name."""
    ascii_name = (name or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-").lower()
    return s or "klijent"


def _client_root(cfg, client_id, name: str) -> str:
    """Absolute path for a client's NAS folder: {root}/klijenti/{id}_{slug}.
    SECURITY: realpath+commonpath guard — the result must resolve inside
    nas_root (or data_dir when nas_root is unset), never escape it."""
    base = cfg.nas_root or cfg.data_dir
    root = os.path.realpath(base)
    folder = f"{client_id}_{_slug(name)}"
    dest = os.path.realpath(os.path.join(root, "klijenti", folder))
    if os.path.commonpath([dest, root]) != root:
        raise ValueError(f"path traversal blocked: {name!r} escapes root")
    return dest


def create_client(spine, cfg, data: dict, owner: str) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("ime klijenta je obavezno")
    oib = (data.get("oib") or "").strip() or None
    if oib and not security.oib_valid(oib):
        raise ValueError("neispravan OIB")

    # ponytail: INSERT + nas_folder UPDATE must land in the SAME transaction —
    # otherwise there's a window where the row exists with nas_folder="" and a
    # concurrent add_document for this client_id would resolve to nas_root
    # itself (defeats per-client folder isolation). Disk folder is created
    # only after that transaction commits.
    root = os.path.realpath(cfg.nas_root or cfg.data_dir)
    try:
        with spine.write() as c:
            client_id = c.execute(
                """INSERT INTO clients(name,oib,email,phone,owner,industry,pdv_status,pausal_eur)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (name, oib, data.get("email") or "", data.get("phone") or "", owner,
                 data.get("industry") or "", data.get("pdv_status") or "",
                 data.get("pausal_eur") or 0),
            ).lastrowid
            folder_path = _client_root(cfg, client_id, name)
            nas_folder = os.path.relpath(folder_path, root)
            c.execute("UPDATE clients SET nas_folder=? WHERE id=?", (nas_folder, client_id))
    except sqlite3.IntegrityError as e:
        raise ValueError("klijent s tim OIB-om već postoji") from e

    os.makedirs(folder_path, exist_ok=True)
    spine.audit(owner, "client_create", str(client_id), name)
    return {"id": client_id, "name": name, "nas_folder": nas_folder, "folder_path": folder_path}


def _client_dir(spine, cfg, client_id) -> str:
    row = spine.read().execute(
        "SELECT nas_folder FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"nepoznat klijent: {client_id}")
    root = os.path.realpath(cfg.nas_root or cfg.data_dir)
    client_dir = os.path.realpath(os.path.join(root, row["nas_folder"] or ""))
    if os.path.commonpath([client_dir, root]) != root:
        raise ValueError("path traversal blocked")
    return client_dir


def add_document(spine, cfg, client_id, filename, data: bytes, owner: str = "") -> dict:
    client_dir = _client_dir(spine, cfg, client_id)

    safe_name = os.path.basename(filename or "").strip()
    ext = os.path.splitext(safe_name)[1].lower()
    if not safe_name or ext not in _EXT_ALLOW:
        raise ValueError(f"nedozvoljen naziv/ekstenzija dokumenta: {filename!r}")

    os.makedirs(client_dir, exist_ok=True)

    # collision guard: never silently overwrite an existing upload — uniquify
    # like sop_images.add_image does (stem_2.ext, stem_3.ext, ...).
    stem, ext = os.path.splitext(safe_name)
    n = 1
    while os.path.exists(os.path.join(client_dir, safe_name)):
        n += 1
        safe_name = f"{stem}_{n}{ext}"

    dest = os.path.realpath(os.path.join(client_dir, safe_name))
    if os.path.commonpath([dest, client_dir]) != client_dir:
        raise ValueError("path traversal blocked")

    with open(dest, "wb") as f:
        f.write(data)

    # ponytail: an ingest failure (unsupported format, parse error) must never
    # lose the file already written to disk — degrade to doc_id=None.
    try:
        doc_id = ingest_file(spine, dest, client_id=client_id)
    except Exception as e:
        _log.warning("ingest failed for %s: %s", dest, e)
        doc_id = None

    spine.audit(owner, "client_document_add", str(client_id), safe_name)
    return {"path": dest, "doc_id": doc_id}


def list_documents(spine, cfg, client_id) -> list[dict]:
    client_dir = _client_dir(spine, cfg, client_id)
    if not os.path.isdir(client_dir):
        return []

    ingested_paths = {
        r["path"] for r in spine.read().execute(
            "SELECT path FROM documents WHERE client_id=?", (client_id,)
        ).fetchall()
    }

    out = []
    for fname in sorted(os.listdir(client_dir)):
        fpath = os.path.join(client_dir, fname)
        if not os.path.isfile(fpath):
            continue
        out.append({
            "filename": fname,
            "path": fpath,
            "size": os.path.getsize(fpath),
            "ingested": fpath in ingested_paths,
        })
    return out


def onboard(spine, cfg, data: dict, documents: list[tuple[str, bytes]], owner: str) -> dict:
    client = create_client(spine, cfg, data, owner)
    docs = []
    ingested = 0
    for filename, blob in documents:
        d = add_document(spine, cfg, client["id"], filename, blob, owner=owner)
        docs.append(d)
        if d["doc_id"] is not None:
            ingested += 1
    return {"client": client, "documents": docs, "ingested": ingested}
