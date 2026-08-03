# Multi-tenancy: organizacije, članstva (uloge), timovi. Substrat za dijeljenje
# memorijskih sredstava po organizaciji i timu.

from ragspine.business.acl import ROLE_RANK, Actor


def create_org(spine, name: str, owner_user_id: int, user: str = "?") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("naziv organizacije je obavezan")
    with spine.write() as c:
        org_id = c.execute("INSERT INTO orgs(name) VALUES(?)", (name,)).lastrowid
        c.execute("INSERT INTO memberships(org_id, user_id, role) VALUES(?,?,'owner')",
                  (org_id, owner_user_id))
    spine.audit(user, "org_create", f"org:{org_id}")
    return org_id


def add_member(spine, org_id: int, user_id: int, role: str = "member", user: str = "?") -> None:
    if role not in ROLE_RANK:
        raise ValueError(f"nepoznata uloga: {role!r}")
    with spine.write() as c:
        c.execute("""INSERT INTO memberships(org_id, user_id, role) VALUES(?,?,?)
                     ON CONFLICT(org_id, user_id) DO UPDATE SET role=excluded.role""",
                  (org_id, user_id, role))
    spine.audit(user, "org_add_member", f"org:{org_id}:user:{user_id}:{role}")


def role_of(spine, org_id: int, user_id: int) -> str | None:
    r = spine.read().execute(
        "SELECT role FROM memberships WHERE org_id=? AND user_id=?", (org_id, user_id)).fetchone()
    return r["role"] if r else None


def list_members(spine, org_id: int) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        """SELECT m.user_id, m.role, u.username FROM memberships m
           JOIN users u ON u.id = m.user_id WHERE m.org_id=? ORDER BY u.username""",
        (org_id,)).fetchall()]


def create_team(spine, org_id: int, name: str, user: str = "?") -> int:
    with spine.write() as c:
        tid = c.execute("INSERT INTO teams(org_id, name) VALUES(?,?)", (org_id, name)).lastrowid
    spine.audit(user, "team_create", f"team:{tid}")
    return tid


def add_to_team(spine, team_id: int, user_id: int, user: str = "?") -> None:
    with spine.write() as c:
        c.execute("INSERT OR IGNORE INTO team_members(team_id, user_id) VALUES(?,?)",
                  (team_id, user_id))
    spine.audit(user, "team_add", f"team:{team_id}:user:{user_id}")


def _team_ids(spine, org_id: int, user_id: int) -> set:
    rows = spine.read().execute(
        """SELECT tm.team_id FROM team_members tm JOIN teams t ON t.id = tm.team_id
           WHERE tm.user_id=? AND t.org_id=?""", (user_id, org_id)).fetchall()
    return {r["team_id"] for r in rows}


def default_org_id(spine) -> int:
    """ID zadane organizacije; ako nijedna ne postoji, kreira 'Ured' (bootstrap
    jedno-uredske instalacije bez ikakvog setup koraka)."""
    r = spine.read().execute("SELECT MIN(id) AS id FROM orgs").fetchone()
    if r["id"] is not None:
        return r["id"]
    with spine.write() as c:
        return c.execute("INSERT INTO orgs(name) VALUES('Ured')").lastrowid


def resolve_login_org(spine, user_id: int, sys_role: str = "") -> tuple[int, str]:
    """(org_id, role) za login: prvo postojeće članstvo; inače upiši članstvo u
    default org — prazan org → owner, sistemski admin → admin, inače member."""
    m = spine.read().execute(
        "SELECT org_id, role FROM memberships WHERE user_id=? ORDER BY id LIMIT 1",
        (user_id,)).fetchone()
    if m:
        return m["org_id"], m["role"]
    org_id = default_org_id(spine)
    empty = spine.read().execute(
        "SELECT 1 FROM memberships WHERE org_id=? LIMIT 1", (org_id,)).fetchone() is None
    role = "owner" if empty else ("admin" if sys_role == "admin" else "member")
    add_member(spine, org_id, user_id, role, user="system")
    return org_id, role


def actor_for(spine, org_id: int, user_id: int) -> Actor | None:
    """Actor s ulogom + timovima za trenutnog korisnika u org-u; None ako nije član."""
    role = role_of(spine, org_id, user_id)
    if role is None:
        return None
    return Actor(user_id=user_id, org_id=org_id, role=role,
                 team_ids=_team_ids(spine, org_id, user_id))
