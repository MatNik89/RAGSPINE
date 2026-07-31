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
