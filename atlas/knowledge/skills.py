# Skills (TIER 1) — verzionirana izvršna sredstva; ideja posuđena iz Hermes/Tencent
# Skill-modela, reimplementirano čisto + multi-tenant. Skill nije samo prompt: ima
# opis, granicu okidanja (trigger), korake i validaciju, verziju i status. Org-scoped,
# dijeljivo po vidljivosti (private/team/org/restricted) + ACL.

import re

STATUSES = ("draft", "active", "archived")
_COLS = ("id", "org_id", "name", "description", "trigger", "steps", "validation",
         "version", "status", "owner_user_id", "visibility", "team_id", "updated_at")


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) >= 3}


def create_skill(spine, org_id: int, name: str, description: str = "", trigger: str = "",
                 steps: str = "", validation: str = "", owner_user_id: int = 0,
                 visibility: str = "private", user: str = "?") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("naziv skilla je obavezan")
    # cap duljine + broja (anti DoS/context-exhaustion: aktivne vještine idu u
    # agent-prompt; neomeđen unos bi napunio SQLite i pojeo kontekst; Codex)
    name = name[:120]
    description, trigger = (description or "")[:300], (trigger or "")[:300]
    steps, validation = (steps or "")[:8000], (validation or "")[:2000]
    with spine.write() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM skills WHERE org_id=?", (org_id,)).fetchone()["n"]
        if n >= 500:
            raise ValueError("dosegnut najveći broj vještina za organizaciju")
        sid = c.execute(
            """INSERT INTO skills(org_id, name, description, trigger, steps, validation,
                 owner_user_id, visibility) VALUES(?,?,?,?,?,?,?,?)""",
            (org_id, name, description, trigger, steps, validation, owner_user_id, visibility),
        ).lastrowid
    spine.audit(user, "skill_create", f"skill:{sid}")
    return sid


def update_skill(spine, skill_id: int, user: str = "?", **fields) -> dict:
    allowed = {"name", "description", "trigger", "steps", "validation", "visibility"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?"); vals.append(v)
    with spine.write() as c:
        if c.execute("SELECT 1 FROM skills WHERE id=?", (skill_id,)).fetchone() is None:
            raise ValueError(f"nepoznat skill: {skill_id}")
        if sets:
            c.execute(f"UPDATE skills SET {', '.join(sets)}, version=version+1, "
                      f"updated_at=datetime('now') WHERE id=?", (*vals, skill_id))
    spine.audit(user, "skill_update", f"skill:{skill_id}")
    return get_skill(spine, skill_id)


def set_status(spine, skill_id: int, status: str, user: str = "?") -> None:
    if status not in STATUSES:
        raise ValueError(f"nepoznat status: {status!r}")
    with spine.write() as c:
        if c.execute("SELECT 1 FROM skills WHERE id=?", (skill_id,)).fetchone() is None:
            raise ValueError(f"nepoznat skill: {skill_id}")
        c.execute("UPDATE skills SET status=?, updated_at=datetime('now') WHERE id=?",
                  (status, skill_id))
    spine.audit(user, "skill_status", f"skill:{skill_id}:{status}")


def get_skill(spine, skill_id: int) -> dict | None:
    r = spine.read().execute(
        "SELECT id, org_id, name, description, trigger, steps, validation, version, "
        "status, owner_user_id, visibility, team_id, updated_at FROM skills WHERE id=?",
        (skill_id,)).fetchone()
    return dict(r) if r else None


def list_skills(spine, org_id: int, status: str | None = None) -> list[dict]:
    q = f"SELECT {', '.join(_COLS)} FROM skills WHERE org_id=?"
    args = [org_id]
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY name COLLATE NOCASE"
    return [dict(r) for r in spine.read().execute(q, args).fetchall()]


def mark_used(spine, skill_id: int) -> None:
    """Zabilježi da je vještina UČITANA (agent povukao korake). use_count hrani
    skill-health (mrtve vs žive procedure). Simetrično memorijskom recall_count."""
    with spine.write() as c:
        c.execute("UPDATE skills SET use_count=COALESCE(use_count,0)+1, "
                  "last_used_at=datetime('now') WHERE id=?", (skill_id,))


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def health(spine, org_id: int, dup_thresh: float = 0.7) -> dict:
    """Izvještaj o zdravlju kataloga vještina — SAMO uvid, NE automatsko brisanje
    (vještine su ljudske uredske procedure; auto-prune bi mogao izgubiti korisno).
    Flagira: mrtve (aktivne, use_count=0), skoro-duplikate (naziv+opis Jaccard),
    manjkave (aktivne bez koraka / prekratki koraci). Owner odlučuje."""
    rows = [dict(r) for r in spine.read().execute(
        "SELECT id, name, description, steps, use_count FROM skills "
        "WHERE org_id=? AND status='active' ORDER BY name COLLATE NOCASE", (org_id,)).fetchall()]
    dead = [{"id": s["id"], "name": s["name"]} for s in rows if not (s.get("use_count") or 0)]
    malformed = [{"id": s["id"], "name": s["name"]}
                 for s in rows if len((s.get("steps") or "").strip()) < 10]
    dups = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            score = _jaccard(f"{a['name']} {a['description']}", f"{b['name']} {b['description']}")
            if score >= dup_thresh:
                dups.append({"a": {"id": a["id"], "name": a["name"]},
                             "b": {"id": b["id"], "name": b["name"]},
                             "slicnost": round(score, 2)})
    return {"aktivnih": len(rows), "mrtve": dead, "duplikati": dups, "manjkave": malformed}


def readable(rows: list[dict], actor) -> list[dict]:
    """Vidljivost-filtar preko ACL fast patha (private/team/org + owner/admin).
    ponytail: 'restricted' skill bez učitanog ACL-a ostaje skriven — upgrade
    path je acl.can() po retku kad restricted skillovi počnu postojati u praksi."""
    from atlas.business.acl import Asset, check
    return [s for s in rows if check(actor, Asset(
        "skill", s["id"], s["org_id"], s["owner_user_id"] or 0,
        s["visibility"] or "org", s["team_id"]), "read")]


def match(spine, org_id: int, query: str, k: int = 3, actor=None) -> list[dict]:
    """Aktivni skillovi čiji naziv/okidač/opis leksički preklapaju upit. Org-scoped;
    s actorom dodatno filtrirano po vidljivosti."""
    qt = _tokens(query)
    if not qt:
        return []
    rows = list_skills(spine, org_id, status="active")
    if actor is not None:
        rows = readable(rows, actor)
    scored = []
    for s in rows:
        st = _tokens(s["name"]) | _tokens(s["trigger"]) | _tokens(s["description"])
        overlap = len(qt & st)
        if overlap:
            scored.append((overlap, s))
    scored.sort(key=lambda x: (-x[0], x[1]["name"].lower()))
    return [s for _, s in scored[:k]]
