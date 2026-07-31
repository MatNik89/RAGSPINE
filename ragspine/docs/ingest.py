"""Document ingest pipeline: parser ladder, chunker, dedup, bulk."""
import hashlib, os, re
from pathlib import Path

from ragspine.core import optional


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


def ingest_text(spine, text: str, title: str, doc_type: str | None = None,
                 client_id=None, source_url: str = "", path: str = ""):
    sha = _norm_sha(text)
    if spine.read().execute("SELECT id FROM documents WHERE sha256=?", (sha,)).fetchone():
        return None
    dtype = doc_type or detect_doc_type(text, path or title)
    with spine.write() as c:
        doc_id = c.execute(
            "INSERT INTO documents(title,path,doc_type,client_id,sha256,source_url) VALUES(?,?,?,?,?,?)",
            (title, path, dtype, client_id, sha, source_url),
        ).lastrowid
        ids = []
        for seq, chunk in enumerate(chunk_text(text)):
            cid = c.execute(
                "INSERT INTO chunks(doc_id,seq,text,title) VALUES(?,?,?,?)",
                (doc_id, seq, chunk, title),
            ).lastrowid
            ids.append(cid)

    try:
        from ragspine.rag import embed
        embed.index_chunks(spine, ids)
    except Exception:
        pass
    try:
        from ragspine.rag import graphrag
        graphrag.index_doc(spine, doc_id, text)
    except Exception:
        pass
    return doc_id


def ingest_file(spine, path: str, client_id=None):
    text = extract_text(path)
    return ingest_text(spine, text, os.path.basename(path), client_id=client_id, path=path)


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
