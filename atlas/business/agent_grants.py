"""Persistent approval grants: "approve once, remember" for a repeatable action
(MateClaw ApprovalGrant + AutoGrantSafetyFloor, adapted for ATLAS).

SAFETY FLOOR (hard, immutable): a HIGH-risk tool (external side effect -- a
message to a client, powering on/waking a workstation, fetching from the web)
NEVER passes automatically, even with an existing grant. Auto-approval is
possible ONLY for low/med.

A grant binds: scope (user|org) + the EXACT tool + the EXACT target
(client/obligation) + max_risk (low|med) + expiry. Exact-target: without a clear
target arg, the entire set of arguments is used (never "any target for this
tool")."""
import json

from atlas.business.acl import ROLE_RANK

_RANK = {"low": 0, "med": 1, "high": 2}

# target keys per tool (action identity); if absent -> the whole args (exact).
# NOTE: oznaci_obvezu/zakazi_rok DELIBERATELY do not include period/date -- the
# grant is "for this client+type across periods" (recurrence is the point of
# "remember"); the actions are reversible+internal and the safety floor
# guarantees they never trigger an external effect.
_TARGET_KEYS = {
    "oznaci_obvezu": ("klijent", "vrsta"),
    "zakazi_rok": ("klijent", "vrsta"),
    "zapisi_belesku": ("klijent",),
    "uredi_klijenta": ("kljuc",),
    "dodaj_klijenta": ("naziv",),
    "dodaj_vrstu_obveze": ("kind",),
    "izvezi_excel": ("sto",),
    "predlozi_vjestinu": ("ime",),
}


def target_for(name: str, args: dict) -> str:
    """Canonical target JSON (no '|' collision; Codex). For tools with target
    keys take that subset, otherwise the whole args (exact-target)."""
    keys = _TARGET_KEYS.get(name)
    src = {k: (args or {}).get(k, "") for k in keys} if keys else (args or {})
    return json.dumps(src, sort_keys=True, ensure_ascii=False, default=str)


def _target_empty(name: str, args: dict) -> bool:
    """True if the target has no significant value (e.g. dodaj_klijenta without
    a name) -> the grant would be a wildcard; that is REJECTED (Codex)."""
    keys = _TARGET_KEYS.get(name)
    if not keys:
        return not (args or {})  # without target keys: empty args = wildcard
    return not any(str((args or {}).get(k, "")).strip() for k in keys)


def can_auto_approve(spine, actor, name: str, args: dict) -> bool:
    """May `name(args)` be executed automatically without confirmation? ONLY if:
    the risk is not high (safety floor) AND a valid grant exists
    (scope/tool/target/risk)."""
    from atlas.rag import agent_tools
    risk = agent_tools.risk(name)
    if _RANK.get(risk, 2) >= _RANK["high"]:
        return False  # SAFETY FLOOR: high is never auto
    target = target_for(name, args)
    row = spine.read().execute(
        """SELECT 1 FROM agent_grants
           WHERE revoked=0 AND tool=? AND org_id=?
             AND (scope='org' OR (scope='user' AND user_id=?))
             AND (target='' OR target=?)
             AND (expire_at IS NULL OR expire_at > datetime('now'))
             AND CASE max_risk WHEN 'low' THEN 0 WHEN 'med' THEN 1 ELSE 2 END >= ?
           LIMIT 1""",
        (name, actor.org_id, actor.user_id, target, _RANK.get(risk, 2))).fetchone()
    return row is not None


def create_grant(spine, actor, name: str, args: dict, scope: str = "user",
                 days: int | None = None, user: str = "?") -> int:
    """Create a grant for (tool, target). max_risk = the tool's risk; REJECT if
    high (safety floor: high cannot even be remembered). org-scope requires
    owner/admin (checked in the route). `days` None = permanent."""
    from atlas.rag import agent_tools
    if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["member"]:
        raise ValueError("za pravilo odobrenja potrebna je barem member uloga")  # Codex: at least member role required for an approval rule
    if name not in agent_tools.TOOLS:
        raise ValueError(f"nepoznat alat: {name!r}")
    risk = agent_tools.risk(name)
    if _RANK.get(risk, 2) >= _RANK["high"]:
        raise ValueError("radnja visokog rizika ne može se automatski odobriti (uvijek traži potvrdu)")
    if scope not in ("user", "org"):
        raise ValueError("scope mora biti 'user' ili 'org'")
    # the grant carries ONLY the target (client/obligation), not the full call ->
    # we do not validate all required args; it is enough that the target is NOT
    # empty (otherwise a wildcard; Codex)
    if _target_empty(name, args):
        raise ValueError("pravilo mora imati konkretan cilj (nije dopušten wildcard)")
    target = target_for(name, args)
    expire = None if not days else f"datetime('now', '+{int(days)} days')"
    with spine.write() as c:
        exp_val = c.execute(f"SELECT {expire} AS e").fetchone()["e"] if expire else None
        gid = c.execute(
            "INSERT INTO agent_grants(org_id,scope,user_id,tool,target,max_risk,expire_at,created_by) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (actor.org_id, scope, actor.user_id, name, target, risk, exp_val, user)).lastrowid
    spine.audit(user, "grant_create", f"{name}:{scope}", target[:100])
    return gid


def list_grants(spine, actor) -> list[dict]:
    """Grants that apply to this actor (their own user + org), not revoked."""
    rows = spine.read().execute(
        "SELECT id, scope, tool, target, max_risk, expire_at, created_by, created_at "
        "FROM agent_grants WHERE revoked=0 AND org_id=? "
        "AND (scope='org' OR (scope='user' AND user_id=?)) ORDER BY id DESC",
        (actor.org_id, actor.user_id)).fetchall()
    # an org target may contain a client's name/OIB that a restricted worker must
    # not see -> mask it for non-admins (Codex; the actor's own user grants stay visible)
    is_admin = ROLE_RANK.get(actor.role, 0) >= ROLE_RANK["admin"]
    out = []
    for r in rows:
        d = dict(r)
        if d["scope"] == "org" and not is_admin:
            d["target"] = "…"
        out.append(d)
    return out


def revoke_grant(spine, actor, grant_id: int, is_owner: bool, user: str = "?") -> bool:
    """Revoke a grant. The grant's owner may revoke their own user grant; an org grant only the owner."""
    row = spine.read().execute(
        "SELECT scope, user_id FROM agent_grants WHERE id=? AND org_id=? AND revoked=0",
        (grant_id, actor.org_id)).fetchone()
    if row is None:
        return False
    if row["scope"] == "org" and not is_owner:
        raise ValueError("org-grant opoziva samo vlasnik")
    if row["scope"] == "user" and row["user_id"] != actor.user_id and not is_owner:
        raise ValueError("tuđi grant ne možete opozvati")
    with spine.write() as c:
        c.execute("UPDATE agent_grants SET revoked=1 WHERE id=? AND org_id=?", (grant_id, actor.org_id))
    spine.audit(user, "grant_revoke", f"grant:{grant_id}")
    return True
