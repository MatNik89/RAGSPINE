# Multi-tenant model dozvola (TIER 0). Čista funkcija check() + DB-backed can()
# s "empty-first fast path" (ACL tablica se čita tek kad zatreba). Tenant-izolacija
# je prvo i tvrdo pravilo — cross-org nikad nije dozvoljen.

from dataclasses import dataclass, field

ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
_ADMIN = ROLE_RANK["admin"]
ACTIONS = ("read", "write", "manage", "delete")
VISIBILITIES = ("private", "team", "org", "restricted")
SUBJECT_TYPES = ("user", "team", "role")


@dataclass
class Actor:
    user_id: int
    org_id: int
    role: str = "member"
    team_ids: set = field(default_factory=set)


@dataclass
class Asset:
    asset_type: str
    asset_id: int
    org_id: int
    owner_user_id: int
    visibility: str = "private"
    team_id: int | None = None


def _acl_matches(row: dict, actor: Actor) -> bool:
    st, sid = row["subject_type"], str(row["subject_id"])
    if st == "user":
        return sid == str(actor.user_id)
    if st == "team":
        return sid in {str(t) for t in actor.team_ids}
    if st == "role":
        return sid == actor.role
    return False


def check(actor: Actor, asset: Asset, action: str, acl_rows: list[dict] | None = None) -> bool:
    """Čista odluka. acl_rows=None znači 'ACL još nije učitan' (fast path)."""
    if actor.org_id != asset.org_id:
        return False  # tvrda tenant-izolacija — nikad cross-org
    if actor.user_id == asset.owner_user_id:
        return True  # vlasnik uvijek sve
    if ROLE_RANK.get(actor.role, 0) >= _ADMIN:
        return True  # admin/owner org-scope
    if action == "read":
        if asset.visibility == "org":
            return True
        if asset.visibility == "team" and asset.team_id in actor.team_ids:
            return True
    for row in (acl_rows or []):
        if _acl_matches(row, actor) and row["permission"] in (action, "manage"):
            return True
    return False


def load_acl(spine, asset_type: str, asset_id: int) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        "SELECT subject_type, subject_id, permission FROM asset_acl "
        "WHERE asset_type=? AND asset_id=?", (asset_type, asset_id)).fetchall()]


def can(spine, actor: Actor, asset: Asset, action: str) -> bool:
    """Fast path bez ACL-a; tek na promašaj (i to samo kad ACL može pomoći) čita bazu."""
    if check(actor, asset, action, acl_rows=None):
        return True
    if asset.visibility == "restricted" or action in ("write", "delete", "manage"):
        return check(actor, asset, action, load_acl(spine, asset.asset_type, asset.asset_id))
    return False


def grant(spine, asset_type: str, asset_id: int, subject_type: str, subject_id,
          permission: str, user: str = "?") -> None:
    if subject_type not in SUBJECT_TYPES:
        raise ValueError(f"nepoznat subject_type: {subject_type!r}")
    if permission not in ACTIONS:
        raise ValueError(f"nepoznata dozvola: {permission!r}")
    with spine.write() as c:
        c.execute("""INSERT OR IGNORE INTO asset_acl(asset_type, asset_id, subject_type, subject_id, permission)
                     VALUES(?,?,?,?,?)""", (asset_type, asset_id, subject_type, str(subject_id), permission))
    spine.audit(user, "acl_grant", f"{asset_type}:{asset_id}:{subject_type}:{subject_id}:{permission}")


def revoke(spine, asset_type: str, asset_id: int, subject_type: str, subject_id,
           permission: str | None = None, user: str = "?") -> None:
    with spine.write() as c:
        if permission is None:
            c.execute("DELETE FROM asset_acl WHERE asset_type=? AND asset_id=? AND subject_type=? AND subject_id=?",
                      (asset_type, asset_id, subject_type, str(subject_id)))
        else:
            c.execute("DELETE FROM asset_acl WHERE asset_type=? AND asset_id=? AND subject_type=? AND subject_id=? AND permission=?",
                      (asset_type, asset_id, subject_type, str(subject_id), permission))
    spine.audit(user, "acl_revoke", f"{asset_type}:{asset_id}:{subject_type}:{subject_id}")
