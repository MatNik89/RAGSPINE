from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ragspine.core import optional
from ragspine.core.security import jwt_encode, verify_password
from ragspine.web.deps import require_user


class LoginBody(BaseModel):
    username: str
    password: str


def create_app(spine, cfg) -> FastAPI:
    app = FastAPI()
    app.state.spine = spine
    app.state.cfg = cfg

    @app.get("/health")
    def health():
        return {"status": "ok", "missing": optional.missing()}

    @app.post("/auth/login")
    def login(body: LoginBody):
        row = spine.read().execute(
            "SELECT pw_hash, role FROM users WHERE username=?", (body.username,)
        ).fetchone()
        if row is None or not verify_password(body.password, row["pw_hash"]):
            raise HTTPException(401, "invalid credentials")
        token = jwt_encode({"sub": body.username, "role": row["role"]}, cfg.jwt_secret)
        return {"token": token}

    @app.get("/v1/models")
    def models(user: str = Depends(require_user)):
        return {"object": "list", "data": [{"id": cfg.llm_model or "ragspine", "object": "model"}]}

    return app
