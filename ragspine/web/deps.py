from fastapi import HTTPException, Request

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
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "missing token")
    try:
        payload = jwt_decode(token, request.app.state.cfg.jwt_secret)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return payload["sub"]
