# Registar uredskih uređaja (skeneri + pisači) + sken/print tijek (piece E).
# Discovery nađe kandidate, admin ih doda u registar, radnik pri SVAKOM
# skeniranju/printanju BIRA uređaj. URL uređaja mora biti LAN adresa —
# provjera i pri dodavanju i pri svakoj upotrebi (core.lan, fail-closed).

import datetime
import os
import urllib.parse

from ragspine.core import lan, optional, security

KINDS = ("scanner", "printer")


def list_devices(spine, kind: str | None = None) -> list[dict]:
    q = "SELECT id, kind, name, url, added_by, added_at FROM devices"
    args: tuple = ()
    if kind:
        q += " WHERE kind=?"
        args = (kind,)
    return [dict(r) for r in spine.read().execute(q + " ORDER BY kind, name", args).fetchall()]


def add_device(spine, kind: str, name: str, url: str, user: str = "?") -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind mora biti scanner|printer, ne {kind!r}")
    name = (name or "").strip()
    if not name:
        raise ValueError("naziv uređaja je obavezan")
    p = urllib.parse.urlsplit((url or "").strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError(f"neispravan URL uređaja: {url!r}")
    # javna adresa se odbija VEĆ pri registraciji (i opet pri svakoj upotrebi)
    lan.assert_lan_host(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    with spine.write() as c:
        dev_id = c.execute(
            "INSERT INTO devices(kind, name, url, added_by) VALUES(?,?,?,?)",
            (kind, name, url.strip(), user)).lastrowid
    spine.audit(user, "device_add", f"device:{dev_id}", f"{kind}:{name}")
    return {"id": dev_id, "kind": kind, "name": name, "url": url.strip()}


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
    """Odredište skenova: registrirana mapa role='skener' (re-validirana kao
    klijenti_root — isdir, ne-simlink, unutar mountova); fallback
    {data_dir}/scans (stvara se)."""
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
    # postojeći symlink/junction 'scans' bi preusmjerio skenove van data_dira
    if os.path.islink(fb) or not security.path_under(os.path.realpath(fb), base):
        raise ValueError("scans mapa je preusmjerena izvan data_dir — odbijam pisati")
    return fb


def _docs_to_pdf(docs: list[tuple[bytes, str]]) -> tuple[bytes, int]:
    """Sklopi eSCL dokumente (PDF i/ili slike) u JEDAN PDF. Vraća (pdf, n_pages)."""
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
    """Skeniraj s ODABRANOG uređaja u SCANNER mapu kao jedan PDF."""
    dev = _get(spine, device_id, "scanner")
    dest_dir = scanner_folder(spine, cfg)
    docs = lan.escl_scan(dev["url"])
    pdf, pages = _docs_to_pdf(docs)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # O_EXCL rezervira ime atomično — dva istovremena skena si ne gaze PDF
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
    """Pošalji dokument iz arhive na ODABRANI pisač."""
    from ragspine.docs import ocr as ocr_mod
    dev = _get(spine, device_id, "printer")
    row = spine.read().execute("SELECT path, title FROM documents WHERE id=?",
                               (doc_id,)).fetchone()
    if row is None or not row["path"]:
        raise ValueError(f"dokument bez datoteke na disku: {doc_id}")
    path = ocr_mod.resolve_scoped_path(cfg, row["path"])  # isti scope kao OCR
    with open(path, "rb") as f:
        data = f.read()
    fmt = "application/pdf" if path.lower().endswith(".pdf") else "application/octet-stream"
    lan.ipp_print(dev["url"], data, doc_format=fmt, job_name=row["title"] or "RAGSPINE")
    spine.audit(user, "device_print", f"device:{device_id}", path)
    # iskreno: IPP je PRIHVATIO posao; fizički završetak ne potvrđujemo (v1)
    return {"submitted": True, "device": dev["name"], "title": row["title"]}
