# Registry of office devices (scanners + printers + workstations) + scan/print
# flow (piece E). Discovery finds candidates, the admin adds them to the registry,
# and the worker CHOOSES a device on EVERY scan/print. A device URL must be a LAN
# address - checked both on adding and on every use (core.lan,
# fail-closed). Workstations (Phase 2, section 4) have no eSCL/IPP URL - they have
# a host/MAC and "capabilities" (caps) that phase 5 (the agent) reads via
# device_for_worker; here we only store data, nothing is executed.

import datetime
import json
import os
import urllib.parse

from atlas.core import lan, optional, security

KINDS = ("scanner", "printer", "radna-stanica")

DEFAULT_CAPS = {"shutdown_order": None, "wol": False, "run_programs": False,
                "monitor_only": False}


def _caps_to_json(caps: dict | None) -> str:
    merged = dict(DEFAULT_CAPS)
    if caps:
        merged.update({k: v for k, v in caps.items() if k in DEFAULT_CAPS})
    return json.dumps(merged)


def _caps_from_json(raw: str | None) -> dict:
    merged = dict(DEFAULT_CAPS)
    if raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = {}
        if isinstance(parsed, dict):
            merged.update({k: v for k, v in parsed.items() if k in DEFAULT_CAPS})
    return merged


def _row_to_dict(r: dict) -> dict:
    d = dict(r)
    d["caps"] = _caps_from_json(d.get("caps"))
    return d


def list_devices(spine, kind: str | None = None) -> list[dict]:
    q = ("SELECT id, kind, name, url, added_by, added_at, caps, mac, "
         "worker_username, host FROM devices")
    args: tuple = ()
    if kind:
        q += " WHERE kind=?"
        args = (kind,)
    rows = spine.read().execute(q + " ORDER BY kind, name", args).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_device(spine, kind: str, name: str, url: str = "", user: str = "?",
               *, mac: str | None = None, worker_username: str | None = None,
               host: str | None = None, caps: dict | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind mora biti scanner|printer|radna-stanica, ne {kind!r}")
    name = (name or "").strip()
    if not name:
        raise ValueError("naziv uređaja je obavezan")
    url = (url or "").strip()
    if kind == "radna-stanica":
        # workstations have no eSCL/IPP URL - they use host (the LAN check still
        # applies when a host is entered, guarding against a public address)
        host = (host or "").strip() or None
        if host:
            lan.assert_lan_host(host, 0)
    else:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError(f"neispravan URL uređaja: {url!r}")
        # a public address is rejected ALREADY at registration (and again on every use)
        lan.assert_lan_host(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    caps_json = _caps_to_json(caps)
    with spine.write() as c:
        dev_id = c.execute(
            "INSERT INTO devices(kind, name, url, added_by, mac, worker_username, "
            "host, caps) VALUES(?,?,?,?,?,?,?,?)",
            (kind, name, url, user, (mac or "").strip() or None,
             (worker_username or "").strip() or None, host, caps_json)).lastrowid
    spine.audit(user, "device_add", f"device:{dev_id}", f"{kind}:{name}")
    return _row_to_dict(spine.read().execute(
        "SELECT id, kind, name, url, added_by, added_at, caps, mac, "
        "worker_username, host FROM devices WHERE id=?", (dev_id,)).fetchone())


def update_device(spine, device_id: int, user: str = "?", **fields) -> dict:
    """Partial device update (workstation): name/host/mac/
    worker_username/caps. Only the fields passed in are changed."""
    row = spine.read().execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if row is None:
        raise ValueError(f"nepoznat uređaj: {device_id}")
    sets, args = [], []
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise ValueError("naziv uređaja je obavezan")
        sets.append("name=?"); args.append(name)
    if "host" in fields:
        host = (fields["host"] or "").strip() or None
        if host:
            lan.assert_lan_host(host, 0)
        sets.append("host=?"); args.append(host)
    if "mac" in fields:
        sets.append("mac=?"); args.append((fields["mac"] or "").strip() or None)
    if "worker_username" in fields:
        sets.append("worker_username=?")
        args.append((fields["worker_username"] or "").strip() or None)
    if "caps" in fields:
        sets.append("caps=?"); args.append(_caps_to_json(fields["caps"]))
    if not sets:
        return _row_to_dict(row)
    args.append(device_id)
    with spine.write() as c:
        c.execute(f"UPDATE devices SET {', '.join(sets)} WHERE id=?", args)
    spine.audit(user, "device_update", f"device:{device_id}", ",".join(fields))
    return _row_to_dict(spine.read().execute(
        "SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())


def device_for_worker(spine, username: str) -> dict | None:
    """The workstation bound to a worker (phase 5 uses it for WOL/monitoring)."""
    if not username:
        return None
    r = spine.read().execute(
        "SELECT id, kind, name, url, added_by, added_at, caps, mac, "
        "worker_username, host FROM devices WHERE worker_username=? "
        "AND kind='radna-stanica'", (username,)).fetchone()
    return _row_to_dict(r) if r else None


def remove_device(spine, device_id: int, user: str = "?") -> None:
    with spine.write() as c:
        if c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone() is None:
            raise ValueError(f"nepoznat uređaj: {device_id}")
        c.execute("DELETE FROM devices WHERE id=?", (device_id,))
    spine.audit(user, "device_remove", f"device:{device_id}")


def _get(spine, device_id: int, kind: str) -> dict:
    r = spine.read().execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if r is None:
        raise ValueError(f"nepoznat uređaj: {device_id}")
    if r["kind"] != kind:
        raise ValueError(f"uređaj {r['name']!r} nije {kind}")
    return dict(r)


def scanner_folder(spine, cfg) -> str:
    """Scan destination: the registered folder role='skener' (re-validated like
    klijenti_root - isdir, not a symlink, within the mounts); fallback
    {data_dir}/scans (created)."""
    r = spine.read().execute(
        "SELECT path FROM folders WHERE role='skener' AND enabled=1 "
        "ORDER BY id LIMIT 1").fetchone()
    if r:
        rp = os.path.realpath(r["path"])
        roots = [os.path.realpath(x) for x in (cfg.mount_roots or [])]
        roots.append(os.path.realpath(cfg.nas_root or cfg.data_dir))
        if (os.path.isdir(rp) and not os.path.islink(r["path"])
                and any(b and security.path_under(rp, b) for b in roots)):
            return rp
        raise ValueError(f"skener-mapa nedostupna ili izvan korijena: {r['path']!r}")
    base = os.path.realpath(cfg.data_dir)
    fb = os.path.join(base, "scans")
    os.makedirs(fb, exist_ok=True)
    # an existing 'scans' symlink/junction would redirect scans outside data_dir
    if os.path.islink(fb) or not security.path_under(os.path.realpath(fb), base):
        raise ValueError("scans mapa je preusmjerena izvan data_dir — odbijam pisati")
    return fb


def _docs_to_pdf(docs: list[tuple[bytes, str]]) -> tuple[bytes, int]:
    """Assemble eSCL documents (PDF and/or images) into ONE PDF. Returns (pdf, n_pages)."""
    fitz = optional.need("fitz", "sken u PDF")
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) nije instaliran")
    out = fitz.open()
    for data, ctype in docs:
        if "pdf" in (ctype or "").lower():
            src = fitz.open("pdf", data)
        else:
            img = fitz.open(stream=data, filetype=(ctype or "image/jpeg").split("/")[-1])
            src = fitz.open("pdf", img.convert_to_pdf())
            img.close()
        out.insert_pdf(src)
        src.close()
    n = out.page_count
    pdf = out.tobytes()
    out.close()
    return pdf, n


def scan(spine, cfg, device_id: int, user: str = "?") -> dict:
    """Scan from the CHOSEN device into the SCANNER folder as a single PDF."""
    dev = _get(spine, device_id, "scanner")
    dest_dir = scanner_folder(spine, cfg)
    docs = lan.escl_scan(dev["url"])
    pdf, pages = _docs_to_pdf(docs)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # O_EXCL reserves the name atomically - two concurrent scans do not clobber each other's PDF
    n = 0
    while True:
        suffix = "" if n == 0 else f"_{n + 1}"
        path = os.path.join(dest_dir, f"sken-{stamp}{suffix}.pdf")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            break
        except FileExistsError:
            n += 1
    with os.fdopen(fd, "wb") as f:
        f.write(pdf)
    with spine.write() as c:
        c.execute("INSERT INTO notifications(kind, body) VALUES('scan_done', ?)",
                  (f"Sken gotov ({dev['name']}): {os.path.basename(path)}, {pages} str.",))
    spine.audit(user, "device_scan", f"device:{device_id}", path)
    return {"path": path, "pages": pages, "device": dev["name"]}


def print_doc(spine, cfg, device_id: int, doc_id: int, user: str = "?") -> dict:
    """Send a document from the archive to the CHOSEN printer."""
    from atlas.docs import ocr as ocr_mod
    dev = _get(spine, device_id, "printer")
    row = spine.read().execute("SELECT path, title FROM documents WHERE id=?",
                               (doc_id,)).fetchone()
    if row is None or not row["path"]:
        raise ValueError(f"dokument bez datoteke na disku: {doc_id}")
    path = ocr_mod.resolve_scoped_path(cfg, row["path"])  # same scope as OCR
    with open(path, "rb") as f:
        data = f.read()
    fmt = "application/pdf" if path.lower().endswith(".pdf") else "application/octet-stream"
    lan.ipp_print(dev["url"], data, doc_format=fmt, job_name=row["title"] or "ATLAS")
    spine.audit(user, "device_print", f"device:{device_id}", path)
    # honestly: IPP ACCEPTED the job; we do not confirm physical completion (v1)
    return {"submitted": True, "device": dev["name"], "title": row["title"]}
