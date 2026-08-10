"""Graph RAG: entity extraction -> knowledge graph (kg_nodes/kg_edges) -> multi-hop -> graph lane.

Entities co-occurring in a document get an undirected "co_occurs" edge tagged
with that doc_id; traverse() BFS-walks kg_edges (both directions) so two docs
sharing an entity (e.g. the same OIB) become reachable from one another.
"""
import re

from atlas.core.llm import LLMError, LLMUnavailable
from atlas.core.security import oib_valid
from atlas.rag import budget, composer
from atlas.rag.retrieval import Hit

_MAX_ENTS = 200  # gornja granica entiteta po dokumentu (klika = _MAX_ENTS^2/2 bridova)


def _q_tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) >= 3}


_OIB_RE = re.compile(r"\b\d{11}\b")
_KONTO_RE = re.compile(r"\bkonto\s+(\d{3,4})\b", re.IGNORECASE)
_IZNOS_RE = re.compile(r"\b\d+(?:\.\d{3})*,\d{2}(?:\s?(?:EUR|€))?\b")
_DATUM_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\.?")
_ZAKON_RE = re.compile(r"(?:Zakon|Pravilnik|Odluka)\s+o\s+[\w\s-]+?(?=[,.\n]|$)")


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Pull (kind, value) entity pairs out of text, deduped, order preserved."""
    found: list[tuple[str, str]] = []
    for m in _OIB_RE.finditer(text):
        if oib_valid(m.group()):
            found.append(("oib", m.group()))
    for m in _KONTO_RE.finditer(text):
        found.append(("konto", m.group(1)))
    for m in _IZNOS_RE.finditer(text):
        found.append(("iznos", m.group().strip()))
    for m in _DATUM_RE.finditer(text):
        found.append(("datum", m.group()))
    for m in _ZAKON_RE.finditer(text):
        found.append(("zakon", m.group().strip()))

    seen = set()
    out = []
    for pair in found:
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def _node_id(c, kind: str, value: str) -> int:
    c.execute("INSERT OR IGNORE INTO kg_nodes(kind,value) VALUES(?,?)", (kind, value))
    return c.execute(
        "SELECT id FROM kg_nodes WHERE kind=? AND value=?", (kind, value)
    ).fetchone()["id"]


def index_doc(spine, doc_id, text: str) -> None:
    """Extract entities from text, upsert kg_nodes, link every co-occurring pair.

    Idempotent: node upsert is INSERT OR IGNORE + UNIQUE(kind,value); edges are
    INSERT OR IGNORE keyed on the (src,dst,rel,doc_id) primary key, so re-indexing
    the same doc never duplicates rows.
    """
    # cap entiteta po dokumentu: klika je O(n^2) bridova — jedan golem mail/dokument
    # s tisućama entiteta bi ubacio milijune redova u jednu transakciju (Codex HIGH)
    ents = extract_entities(text)[:_MAX_ENTS]
    if len(ents) < 2:
        if ents:
            with spine.write() as c:
                _node_id(c, *ents[0])
        return
    with spine.write() as c:
        ids = [_node_id(c, kind, value) for kind, value in ents]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                c.execute(
                    "INSERT OR IGNORE INTO kg_edges(src,dst,rel,doc_id) VALUES(?,?,?,?)",
                    (ids[i], ids[j], "co_occurs", doc_id),
                )


def traverse(spine, seeds: list[int], hops: int = 2) -> set[int]:
    """BFS over kg_edges (both directions) from seed node ids, up to `hops` hops."""
    reached = set(seeds)
    frontier = set(seeds)
    conn = spine.read()
    for _ in range(hops):
        if not frontier:
            break
        placeholders = ",".join("?" * len(frontier))
        params = tuple(frontier) * 2
        rows = conn.execute(
            f"SELECT src, dst FROM kg_edges WHERE src IN ({placeholders}) OR dst IN ({placeholders})",
            params,
        ).fetchall()
        next_frontier = set()
        for r in rows:
            for node in (r["src"], r["dst"]):
                if node not in reached:
                    reached.add(node)
                    next_frontier.add(node)
        frontier = next_frontier
    return reached


def handle(spine, cfg, query: str, llm, visible=None, org_id=None) -> str:
    """Graph lane: entities in query -> traverse -> connected docs -> compose+LLM.
    `visible` (skup vidljivih client_id ili None) skriva dokumente klijenata koje
    restringirani radnik ne smije vidjeti. `org_id` scopea na org — kg_edges su
    globalni pa bi traversal inače dosegao dokumente DRUGE organizacije (Codex HIGH)."""
    conn = spine.read()
    ents = extract_entities(query)
    seeds = []
    for kind, value in ents:
        row = conn.execute(
            "SELECT id FROM kg_nodes WHERE kind=? AND value=?", (kind, value)
        ).fetchone()
        if row:
            seeds.append(row["id"])
    if not seeds:
        return "Nema poznatih entiteta u upitu za graf pretragu."

    reached = traverse(spine, seeds, hops=2)
    placeholders = ",".join("?" * len(reached))
    params = tuple(reached) * 2
    doc_ids = {
        r["doc_id"]
        for r in conn.execute(
            f"SELECT DISTINCT doc_id FROM kg_edges WHERE src IN ({placeholders}) OR dst IN ({placeholders})",
            params,
        ).fetchall()
    }
    if not doc_ids:
        return "Nema povezanih dokumenata."

    # org-scope: kg_edges su globalni preko orgova -> zadrži samo dokumente ove
    # organizacije (ili dijeljene org_id IS NULL) prije bilo čega (Codex HIGH)
    if org_id is not None:
        dph = ",".join("?" * len(doc_ids))
        doc_ids = {
            r["id"] for r in conn.execute(
                f"SELECT id FROM documents WHERE id IN ({dph}) "
                f"AND (org_id=? OR org_id IS NULL)", (*doc_ids, org_id)).fetchall()}
        if not doc_ids:
            return "Nema povezanih dokumenata."

    # restringiran radnik: zadrži samo uredske (client_id IS NULL) i vidljive dok.
    if visible is not None:
        dph = ",".join("?" * len(doc_ids))
        vph = ",".join("?" * len(visible)) if visible else "NULL"
        doc_ids = {
            r["id"] for r in conn.execute(
                f"SELECT id FROM documents WHERE id IN ({dph}) "
                f"AND (client_id IS NULL OR client_id IN ({vph}))",
                (*doc_ids, *visible)).fetchall()
        }
        if not doc_ids:
            return "Nema povezanih dokumenata."

    if llm is None:
        ent_list = ", ".join(f"{k}={v}" for k, v in ents)
        docs_list = ", ".join(str(d) for d in sorted(doc_ids))
        return f"Povezani entiteti: {ent_list}. Dokumenti: {docs_list}."

    ph = ",".join("?" * len(doc_ids))
    rows = conn.execute(
        f"""SELECT c.id AS chunk_id, c.doc_id, c.title, c.text, d.doc_type
            FROM chunks c JOIN documents d ON d.id = c.doc_id
            WHERE c.doc_id IN ({ph}) ORDER BY c.doc_id, c.seq""",
        tuple(doc_ids),
    ).fetchall()
    hits = [
        Hit(r["chunk_id"], r["doc_id"], r["title"], r["text"], 1.0, r["doc_type"])
        for r in rows
    ]
    # graf zna vratiti SVE chunkove povezanih dokumenata — bez kompakcije
    # prompt eksplodira (144k znakova u probi). Ali kompakcija čuva PREFIKS, a
    # graf-hitovi su poredani po (doc_id, seq) s uniformnim scoreom — Codex r3:
    # stari/veliki dokumenti pojedu budžet i izbace baš relevantni. Zato prvo
    # leksičko rangiranje prema upitu (stabilno: overlap desc, pa izvorni red).
    qt = _q_tokens(query)
    hits.sort(key=lambda h: -len(qt & _q_tokens(h.text)))
    hits = budget.compact(hits)
    system, messages = composer.compose(query, hits)
    try:
        result = llm.complete(messages, system=system)
    except (LLMUnavailable, LLMError):
        return "LLM trenutno nedostupan ili je vratio grešku."
    return result.text


from atlas.rag import pipeline  # noqa: E402  (lazy: avoid any import-order coupling)
pipeline.LANE_HANDLERS["graph"] = handle  # dispatch prosljeđuje visible (vidi pipeline)
