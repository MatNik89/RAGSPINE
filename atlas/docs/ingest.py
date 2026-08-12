"""Document ingest pipeline: parser ladder, chunker, dedup, bulk."""
import hashlib, os, re, sqlite3
from pathlib import Path

from atlas.business import tenancy
from atlas.core import optional


class UnsupportedFormat(Exception):
    pass


KEYWORDS = {
    "racun": ("račun", "pdv", "iznos"),
    "ugovor": ("ugovor", "sklopljen", "stranke"),
    "bilanca": ("bilanca", "aktiva", "pasiva"),
    "zakon": ("članak", "zakon", "narodne novine", "pravilnik"),
    "sop": ("postupak", "korak", "sop"),
}


def detect_doc_type(text: str, filename: str = "") -> str:
    hay = (text + " " + filename).lower()
    scores = {t: sum(hay.count(k) for k in kws) for t, kws in KEYWORDS.items()}
    best = max(scores.values())
    if best == 0:
        return "ostalo"
    winners = [t for t, s in scores.items() if s == best]
    return winners[0] if len(winners) == 1 else "ostalo"


def _carry(s: str, overlap: int) -> str:
    """Trailing ~overlap chars of s, cut at a word boundary (never mid-word)."""
    if overlap <= 0 or not s:
        return ""
    if len(s) <= overlap:
        return s
    tail = s[-overlap:]
    sp = tail.find(" ")
    return tail[sp + 1:] if sp != -1 else ""


def _split_words(s: str, size: int, overlap: int, buf: str):
    """Accumulate words of s onto buf, flushing whenever size would be exceeded."""
    emitted = []
    for w in s.split():
        piece = (buf + " " + w) if buf else w
        if len(piece) > size and buf:
            emitted.append(buf)
            buf = _carry(buf, overlap)
            piece = (buf + " " + w) if buf else w
        buf = piece
    return emitted, buf


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    text = text.strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        joined = (buf + "\n\n" + para) if buf else para
        if len(joined) <= size:
            buf = joined
            continue
        if buf:
            chunks.append(buf)
            buf = _carry(buf, overlap)
        joined = (buf + "\n\n" + para) if buf else para
        if len(joined) <= size:
            buf = joined
        else:
            emitted, buf = _split_words(para, size, overlap, buf)
            chunks.extend(emitted)
    if buf:
        chunks.append(buf)
    return chunks


def extract_text(path: str) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        fitz = optional.need("fitz", "PDF ingest")
        if fitz is None:
            raise UnsupportedFormat(f"PDF ingest nedostupan: {path}")
        doc = fitz.open(str(p))
        try:
            return "\n\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    if ext == ".docx":
        docx = optional.need("docx", "DOCX ingest")
        if docx is None:
            raise UnsupportedFormat(f"DOCX ingest nedostupan: {path}")
        d = docx.Document(str(p))
        return "\n\n".join(para.text for para in d.paragraphs)
    if ext == ".xlsx":
        openpyxl = optional.need("openpyxl", "XLSX ingest")
        if openpyxl is None:
            raise UnsupportedFormat(f"XLSX ingest nedostupan: {path}")
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    lines.append(" ".join(cells))
        return "\n".join(lines)
    if ext in (".txt", ".md"):
        return p.read_text(encoding="utf-8")
    raise UnsupportedFormat(f"Nepodržan format: {ext}")


def _norm_sha(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text.strip()).encode("utf-8")).hexdigest()


# Codex finding: the prefix is MANDATORY — a bare number ("2024", "1000") or "12/2024"
# is legitimate content (year, amount, accounting period), not pagination.
_PAGENO_RE = re.compile(r"^\s*(?:str(?:anica)?\.?|page)\s*\d{1,4}\s*(?:/|od|of)?\s*\d{0,4}\s*$",
                        re.IGNORECASE)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]")


def _boiler_key(line: str) -> str | None:
    """Key for repeat-collapse ONLY for lines that look like a header/footer:
    15-80 characters, contain letters and have NO digits. Short labels ('Clanak'),
    numeric table lines ('0,00 0,00') and invoice items with amounts
    ('Usluga savjetovanja 100,00') are NOT touched — Codex findings over 2 rounds:
    collapsing them changes their meaning. A header with a number (address, OIB) thus
    survives in the index — a deliberate cost, better noise than lost content."""
    key = " ".join(line.split()).lower()
    if 15 <= len(key) < 80 and _HAS_LETTER_RE.search(key) and not any(c.isdigit() for c in key):
        return key
    return None


def clean_noise(text: str) -> str:
    """Noise cleanup before indexing (TIER 2): explicit pagination lines
    ('Stranica 2/3') removed; a header/footer line that repeats >=3 times is kept
    only the first time."""
    lines = text.splitlines()
    freq: dict[str, int] = {}
    for ln in lines:
        key = _boiler_key(ln)
        if key:
            freq[key] = freq.get(key, 0) + 1
    out, seen_boiler = [], set()
    for ln in lines:
        if _PAGENO_RE.fullmatch(ln):
            continue
        key = _boiler_key(ln)
        if key and freq.get(key, 0) >= 3:
            if key in seen_boiler:
                continue
            seen_boiler.add(key)
        out.append(ln)
    cleaned = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def _file_sha(path: str, bufsize: int = 1 << 20) -> str:
    """SHA-256 of raw file bytes (streamed), distinct from _norm_sha's
    normalized-TEXT hash — this is the vault's move/rename identity key."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_text(spine, text: str, title: str, doc_type: str | None = None,
                 client_id=None, source_url: str = "", path: str = "", org_id=None):
    sha = _norm_sha(text)
    if spine.read().execute("SELECT id FROM documents WHERE sha256=?", (sha,)).fetchone():
        return None
    dtype = doc_type or detect_doc_type(text, path or title)
    if org_id is None:
        org_id = tenancy.default_org_id(spine)  # every insert is always stamped
    with spine.write() as c:
        try:
            doc_id = c.execute(
                "INSERT INTO documents(title,path,doc_type,client_id,sha256,source_url,org_id) VALUES(?,?,?,?,?,?,?)",
                (title, path, dtype, client_id, sha, source_url, org_id),
            ).lastrowid
        except sqlite3.IntegrityError:
            # lost a dedup race: another writer inserted the same sha256 first
            return None
        ids = []
        seen_chunks: set[str] = set()  # dedup ladder, last rung: identical chunk within the document
        seq = 0
        for chunk in chunk_text(clean_noise(text)):
            csha = _norm_sha(chunk)
            if csha in seen_chunks:
                continue
            seen_chunks.add(csha)
            cid = c.execute(
                "INSERT INTO chunks(doc_id,seq,text,title) VALUES(?,?,?,?)",
                (doc_id, seq, chunk, title),
            ).lastrowid
            ids.append(cid)
            seq += 1

    try:
        from atlas.rag import embed
        embed.index_chunks(spine, ids)
    except ImportError:
        pass
    try:
        from atlas.rag import graphrag
        graphrag.index_doc(spine, doc_id, text)
    except ImportError:
        pass
    try:
        from atlas.rag import authority
        authority.index_references(spine, doc_id, text)
    except ImportError:
        pass
    return doc_id


def ingest_file(spine, path: str, client_id=None, doc_type: str | None = None):
    fsha = _file_sha(path)
    text = extract_text(path)
    doc_id = ingest_text(spine, text, os.path.basename(path), doc_type=doc_type,
                         client_id=client_id, path=path)
    if doc_id is not None:
        with spine.write() as c:
            c.execute("UPDATE documents SET file_sha=? WHERE id=?", (fsha, doc_id))
    return doc_id


def bulk_ingest(spine, folder: str) -> dict:
    result = {"ingested": 0, "skipped": 0, "errors": []}
    for root, _, files in os.walk(folder):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                doc_id = ingest_file(spine, fpath)
            except UnsupportedFormat:
                result["skipped"] += 1
                continue
            except Exception as e:
                result["errors"].append(f"{fpath}: {e}")
                continue
            if doc_id is None:
                result["skipped"] += 1
            else:
                result["ingested"] += 1
    return result
