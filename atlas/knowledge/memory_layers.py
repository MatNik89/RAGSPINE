# Slojevita memorija L0→L3 (TIER 1) — ideja iz Tencent memory-huba, reimplementirano
# čisto + multi-tenant. L0 sirovi razgovor → L1 atomi (činjenice/preferencije/odluke,
# LLM izvlači, dedup) → L3 persona (dugoročni profil). Retrieval slojevit + budžetiran
# (broj stavki + znakovi), da memorija ne preplavi kontekst. Org+korisnik scoped.

import json
import re

from atlas.core.llm import LLMError, LLMUnavailable

ATOM_KINDS = ("fact", "preference", "decision", "constraint", "event")

_L1_SYSTEM = (
    "Izvuci trajne memorijske atome iz razgovora. Vrati ISKLJUČIVO JSON niz: "
    "[{\"kind\":\"fact|preference|decision|constraint|event\",\"content\":\"kratka tvrdnja\"}]. "
    "Samo ono što je vrijedno pamtiti za idući put; bez prolaznog blebetanja. Bez teksta izvan JSON-a."
)
_L3_SYSTEM = (
    "Na temelju memorijskih atoma napiši sažet profil korisnika/ureda (persona): "
    "stabilne preferencije, način rada, ključne odluke i ograničenja. Kratko, u natuknicama."
)


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) >= 3}


def _extract_json(raw: str):
    a, b = raw.find("["), raw.rfind("]")
    if a != -1 and b > a:
        try:
            return json.loads(raw[a:b + 1])
        except json.JSONDecodeError:
            pass
    return []


def record_turn(spine, org_id: int, user_id: int, session_id: str, role: str, content: str) -> int:
    with spine.write() as c:
        return c.execute(
            "INSERT INTO mem_l0(org_id, user_id, session_id, role, content) VALUES(?,?,?,?,?)",
            (org_id, user_id, session_id, role, content),
        ).lastrowid


def _existing_atoms(spine, org_id: int, user_id: int) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        "SELECT id, kind, content FROM mem_l1 WHERE org_id=? AND user_id=?",
        (org_id, user_id)).fetchall()]


def _is_dup(content: str, existing: list[dict], thresh: float = 0.6) -> bool:
    ct = _tokens(content)
    if not ct:
        return True
    for e in existing:
        et = _tokens(e["content"])
        if et and len(ct & et) / len(ct | et) >= thresh:  # Jaccard
            return True
    return False


def distill(spine, org_id: int, user_id: int, llm) -> dict:
    """L0 → L1: izvuci atome iz nedestiliranih replika, dedup, spremi; označi L0."""
    if llm is None:
        return {"atoms": 0, "skipped": True}
    rows = spine.read().execute(
        "SELECT id, role, content FROM mem_l0 WHERE org_id=? AND user_id=? AND distilled=0 ORDER BY id",
        (org_id, user_id)).fetchall()
    if not rows:
        return {"atoms": 0}
    convo = "\n".join(f"{r['role']}: {r['content']}" for r in rows)
    try:
        res = llm.complete([{"role": "user", "content": convo}], system=_L1_SYSTEM)
    except (LLMUnavailable, LLMError):
        return {"atoms": 0, "skipped": True}
    existing = _existing_atoms(spine, org_id, user_id)
    added = 0
    with spine.write() as c:
        for a in _extract_json(res.text):
            if not isinstance(a, dict) or not a.get("content"):
                continue
            content = str(a["content"]).strip()
            if _is_dup(content, existing):
                continue
            kind = a.get("kind") if a.get("kind") in ATOM_KINDS else "fact"
            c.execute("INSERT INTO mem_l1(org_id, user_id, kind, content) VALUES(?,?,?,?)",
                      (org_id, user_id, kind, content))
            existing.append({"content": content})
            added += 1
        c.execute("UPDATE mem_l0 SET distilled=1 WHERE org_id=? AND user_id=? AND distilled=0",
                  (org_id, user_id))
    return {"atoms": added}


def build_persona(spine, org_id: int, user_id: int, llm) -> str | None:
    """L1 → L3: sažmi atome u dugoročni profil (persona). Vrati tekst ili None."""
    if llm is None:
        return None
    atoms = _existing_atoms(spine, org_id, user_id)
    if not atoms:
        return None
    block = "\n".join(f"- ({a['kind']}) {a['content']}" for a in atoms)
    try:
        res = llm.complete([{"role": "user", "content": block}], system=_L3_SYSTEM)
    except (LLMUnavailable, LLMError):
        return None
    with spine.write() as c:
        c.execute("""INSERT INTO mem_l3(org_id, user_id, persona, updated_at)
                     VALUES(?,?,?,datetime('now'))
                     ON CONFLICT(org_id, user_id) DO UPDATE SET
                       persona=excluded.persona, updated_at=excluded.updated_at""",
                  (org_id, user_id, res.text))
    return res.text


def recall(spine, org_id: int, user_id: int, query: str,
           max_items: int = 6, max_chars: int = 1200) -> dict:
    """Slojevit dohvat: L3 persona (brzi bootstrap) + top L1 atomi za upit, budžetirano.
    Recall-tracking (MateClaw): atomi koji se STVARNO dohvate dobiju recall_count +
    -> na izjednačenom preklapanju rangiraju se više ('zaslužni' izbiju gore).
    Petlja koja mjeri što se koristi i promovira korisno."""
    p = spine.read().execute(
        "SELECT persona FROM mem_l3 WHERE org_id=? AND user_id=?", (org_id, user_id)).fetchone()
    persona = (p["persona"] if p else "") or ""
    qt = _tokens(query)
    scored = []
    for a in spine.read().execute(
            "SELECT id, content, recall_count FROM mem_l1 WHERE org_id=? AND user_id=?",
            (org_id, user_id)).fetchall():
        overlap = len(qt & _tokens(a["content"]))
        if overlap:  # rang: preklapanje pa recall_count (zaslužni izbiju na izjednačenom)
            scored.append((overlap, a["recall_count"] or 0, a["id"], a["content"]))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    atoms, used, hit_ids = [], 0, []
    for _ov, _rc, aid, content in scored:
        if len(atoms) >= max_items or used + len(content) > max_chars:
            break
        atoms.append(content); used += len(content); hit_ids.append(aid)
    if hit_ids:  # promoviraj STVARNO dohvaćene atome
        ph = ",".join("?" * len(hit_ids))
        with spine.write() as c:
            c.execute(f"UPDATE mem_l1 SET recall_count=COALESCE(recall_count,0)+1, "
                      f"last_recalled_at=datetime('now') WHERE id IN ({ph})", tuple(hit_ids))
    return {"persona": persona[:max_chars], "atoms": atoms}
