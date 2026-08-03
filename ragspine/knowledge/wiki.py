# LLM-održavani Wiki (TIER 1) — ideja posuđena od Tencent memory-huba / Karpathy
# "LLM Wiki", reimplementirano čisto i multi-tenant. Sirovi dokument → LLM sintetizira
# tipizirane, međusobno povezane ([[wikilink]]) stranice. Sha-inkrementalno (re-LLM samo
# promijenjeni izvor); 'locked' stranice (ručno uređene) se NIKAD ne gaze; sve org-scoped.

import hashlib
import json
import re
import unicodedata

from ragspine.core.llm import LLMError, LLMUnavailable
from ragspine.rag import retrieval

PAGE_TYPES = ("entity", "concept", "source", "synthesis")
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

_SYSTEM = (
    "Ti si urednik baze znanja. Iz priloženog teksta izvuci strukturirane wiki-stranice. "
    "Vrati ISKLJUČIVO JSON niz objekata: [{\"type\":\"entity|concept|source|synthesis\","
    "\"title\":\"...\",\"body\":\"markdown, poveznice na druge stranice u obliku [[Naslov]]\"}]. "
    "Bez teksta izvan JSON-a. Naslovi kratki i jedinstveni."
)


def _slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "bez-naslova"


def _slug(page_type: str, title: str) -> str:
    t = page_type if page_type in PAGE_TYPES else "concept"
    return f"{t}/{_slugify(title)}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _extract_json(raw: str):
    """Robusno izvuci JSON niz iz LLM izlaza (podnosi ograde/fences)."""
    a, b = raw.find("["), raw.rfind("]")
    if a != -1 and b > a:
        try:
            return json.loads(raw[a:b + 1])
        except json.JSONDecodeError:
            pass
    return []


def synthesize(llm, text: str) -> list[dict]:
    if llm is None:
        return []
    try:
        res = llm.complete([{"role": "user", "content": text}], system=_SYSTEM)
    except (LLMUnavailable, LLMError):
        return []
    pages = []
    for p in _extract_json(res.text):
        if isinstance(p, dict) and p.get("title"):
            pages.append({"type": p.get("type", "concept"), "title": str(p["title"]),
                          "body": str(p.get("body", ""))})
    return pages


def _link_slugs(body: str) -> set:
    return {_slugify(m) for m in _LINK_RE.findall(body or "")}


def _upsert_page(spine, org_id: int, page: dict, owner_user_id: int, visibility: str) -> tuple[int, bool]:
    """Vrati (page_id, upserted?). Locked stranica se ne dira."""
    slug = _slug(page["type"], page["title"])
    with spine.write() as c:
        row = c.execute("SELECT id, locked, version FROM wiki_pages WHERE org_id=? AND slug=?",
                        (org_id, slug)).fetchone()
        if row and row["locked"]:
            return row["id"], False  # ručno uređena — čuvamo
        if row:
            pid = row["id"]
            c.execute("""UPDATE wiki_pages SET type=?, title=?, body=?, version=?, updated_at=datetime('now')
                         WHERE id=?""", (page["type"], page["title"], page["body"],
                                         (row["version"] or 1) + 1, pid))
        else:
            pid = c.execute("""INSERT INTO wiki_pages(org_id, type, title, slug, body,
                                 owner_user_id, visibility) VALUES(?,?,?,?,?,?,?)""",
                            (org_id, page["type"], page["title"], slug, page["body"],
                             owner_user_id, visibility)).lastrowid
        c.execute("DELETE FROM wiki_fts WHERE page_id=?", (pid,))
        c.execute("INSERT INTO wiki_fts(page_id, title, body) VALUES(?,?,?)",
                  (pid, page["title"], page["body"]))
        c.execute("DELETE FROM wiki_links WHERE src_page_id=?", (pid,))
        for dst in _link_slugs(page["body"]):
            c.execute("INSERT OR IGNORE INTO wiki_links(org_id, src_page_id, dst_slug) VALUES(?,?,?)",
                      (org_id, pid, dst))
    return pid, True


def ingest_source(spine, org_id: int, source_key: str, text: str, llm,
                  owner_user_id: int = 0, visibility: str = "org") -> dict:
    """Sintetiziraj/aktualiziraj wiki-stranice iz izvora. Sha-inkrementalno."""
    sha = _sha(text)
    prev = spine.read().execute(
        "SELECT sha256 FROM wiki_sources WHERE org_id=? AND source_key=?",
        (org_id, source_key)).fetchone()
    if prev and prev["sha256"] == sha:
        return {"skipped": True, "pages": 0}
    pages = synthesize(llm, text)
    if not pages:
        return {"skipped": False, "pages": 0, "error": "sinteza prazna"}
    n = 0
    for p in pages:
        _pid, upserted = _upsert_page(spine, org_id, p, owner_user_id, visibility)
        n += 1 if upserted else 0
    with spine.write() as c:
        c.execute("""INSERT INTO wiki_sources(org_id, source_key, sha256) VALUES(?,?,?)
                     ON CONFLICT(org_id, source_key) DO UPDATE SET sha256=excluded.sha256""",
                  (org_id, source_key, sha))
    return {"skipped": False, "pages": len(pages), "written": n}


def get_page(spine, org_id: int, slug: str) -> dict | None:
    return _page_by_slug(spine, org_id, slug)


def list_pages(spine, org_id: int) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        "SELECT id, type, title, slug, locked, version, updated_at FROM wiki_pages "
        "WHERE org_id=? ORDER BY title COLLATE NOCASE", (org_id,)).fetchall()]


def _page_by_slug(spine, org_id: int, slug: str) -> dict | None:
    r = spine.read().execute(
        "SELECT id, type, title, slug, body, locked, version FROM wiki_pages WHERE org_id=? AND slug=?",
        (org_id, slug)).fetchone()
    return dict(r) if r else None


def search(spine, org_id: int, query: str, k: int = 5) -> list[dict]:
    """Org-scoped FTS (BM25, naslov važniji) + 1-hop širenje po [[wikilink]] grafu."""
    fts_q = retrieval._fts_query(query)
    if not fts_q:
        return []
    rows = spine.read().execute(
        """SELECT p.id, p.type, p.title, p.slug, p.body,
                  bm25(wiki_fts, 5.0, 1.0) AS rank
           FROM wiki_fts f JOIN wiki_pages p ON p.id = f.page_id
           WHERE wiki_fts MATCH ? AND p.org_id = ?
           ORDER BY rank LIMIT ?""",
        (fts_q, org_id, k),
    ).fetchall()
    out = []
    for r in rows:
        related = [x["dst_slug"] for x in spine.read().execute(
            "SELECT dst_slug FROM wiki_links WHERE src_page_id=?", (r["id"],)).fetchall()]
        out.append({"type": r["type"], "title": r["title"], "slug": r["slug"],
                    "snippet": (r["body"] or "")[:280], "related": related})
    return out


def set_locked(spine, org_id: int, slug: str, locked: bool, user: str = "?") -> None:
    with spine.write() as c:
        if c.execute("SELECT 1 FROM wiki_pages WHERE org_id=? AND slug=?", (org_id, slug)).fetchone() is None:
            raise ValueError(f"nepoznata stranica: {slug}")
        c.execute("UPDATE wiki_pages SET locked=? WHERE org_id=? AND slug=?",
                  (1 if locked else 0, org_id, slug))
    spine.audit(user, "wiki_lock" if locked else "wiki_unlock", f"wiki:{org_id}:{slug}")
