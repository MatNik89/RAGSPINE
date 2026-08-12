from fastapi import HTTPException, Request

from atlas.business import tenancy
from atlas.business.acl import Actor
from atlas.core.security import AuthError, hash_password, jwt_decode


def add_user(spine, username: str, password: str, role: str = "radnik") -> None:
    with spine.write() as c:
        c.execute(
            "INSERT INTO users(username, pw_hash, role) VALUES(?,?,?)",
            (username, hash_password(password), role),
        )


def require_user(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = auth.removeprefix("Bearer ")
    try:
        payload = jwt_decode(token, request.app.state.cfg.jwt_secret)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return payload["sub"]


COOKIE_NAME = "atlas_token"


def require_user_web(request: Request) -> str:
    """Like require_user but also accepts the JWT from an HttpOnly cookie —
    lets a real browser (which can't set Authorization headers on navigation
    or native form submits) use the /obveze HTML page."""
    return _decode_web(request)["sub"]


def _decode_web(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "missing token")
    try:
        return jwt_decode(token, request.app.state.cfg.jwt_secret)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e


def _actor_from_payload(request: Request, payload: dict) -> Actor:
    """Actor from the JWT pointers (uid, org_id). The role is NOT read from the
    token but freshly from memberships — a role change/revocation takes effect
    immediately. An old token without claims (24h transition) is resolved by
    username."""
    spine = request.app.state.spine
    uid, org_id = payload.get("uid"), payload.get("org_id")
    if uid is None or org_id is None:
        row = spine.read().execute(
            "SELECT id FROM users WHERE username=?", (payload["sub"],)).fetchone()
        if row is None:
            raise HTTPException(401, "nepoznat korisnik")
        uid = row["id"]
        # read-only fallback: does NOT create a membership (only login does that) —
        # an old token for a user without membership means re-login, not a write.
        m = spine.read().execute(
            "SELECT org_id FROM memberships WHERE user_id=? ORDER BY id LIMIT 1",
            (uid,)).fetchone()
        if m is None:
            raise HTTPException(401, "prijavite se ponovno")
        org_id = m["org_id"]
    actor = tenancy.actor_for(spine, org_id, uid)
    if actor is None:
        raise HTTPException(403, "niste član organizacije")
    actor.username = payload["sub"]
    # Impersonation: the ceiling is checked on EVERY request (not only at login) —
    # otherwise a later promotion of the target lifts the stored token above the
    # impersonator (Codex HIGH). The impersonator must still exist and STRICTLY outrank the target.
    imp_uid = payload.get("imp_uid")
    if imp_uid is not None:
        from atlas.business.acl import ROLE_RANK
        imp = spine.read().execute(
            "SELECT role FROM memberships WHERE user_id=? AND org_id=?",
            (imp_uid, org_id)).fetchone()
        if imp is None or ROLE_RANK.get(imp["role"], -1) <= ROLE_RANK.get(actor.role, 99):
            raise HTTPException(403, "impersonacija više nije dopuštena — prijavite se ponovno")
    return actor


def require_actor_web(request: Request) -> Actor:
    """Org-aware variant of require_user_web: returns an Actor for ACL decisions."""
    return _actor_from_payload(request, _decode_web(request))


def require_actor(request: Request) -> Actor:
    """Org-aware variant of require_user (Bearer only, for API clients)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        payload = jwt_decode(auth.removeprefix("Bearer "), request.app.state.cfg.jwt_secret)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return _actor_from_payload(request, payload)
