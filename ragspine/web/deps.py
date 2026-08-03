from fastapi import HTTPException, Request

from ragspine.business import tenancy
from ragspine.business.acl import Actor
from ragspine.core.security import AuthError, hash_password, jwt_decode


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


COOKIE_NAME = "ragspine_token"


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
    """Actor iz JWT pokazivača (uid, org_id). Uloga se NE čita iz tokena nego
    svježa iz memberships — promjena/revokacija uloge vrijedi odmah. Stari
    token bez claimova (24h prijelaz) razrješava se po username-u."""
    spine = request.app.state.spine
    uid, org_id = payload.get("uid"), payload.get("org_id")
    if uid is None or org_id is None:
        row = spine.read().execute(
            "SELECT id, role FROM users WHERE username=?", (payload["sub"],)).fetchone()
        if row is None:
            raise HTTPException(401, "nepoznat korisnik")
        uid = row["id"]
        org_id, _ = tenancy.resolve_login_org(spine, uid, row["role"])
    actor = tenancy.actor_for(spine, org_id, uid)
    if actor is None:
        raise HTTPException(403, "niste član organizacije")
    actor.username = payload["sub"]
    return actor


def require_actor_web(request: Request) -> Actor:
    """Org-svjesna varijanta require_user_web: vraća Actor za ACL odluke."""
    return _actor_from_payload(request, _decode_web(request))


def require_actor(request: Request) -> Actor:
    """Org-svjesna varijanta require_user (samo Bearer, za API klijente)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        payload = jwt_decode(auth.removeprefix("Bearer "), request.app.state.cfg.jwt_secret)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return _actor_from_payload(request, payload)
