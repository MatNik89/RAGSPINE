from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ragspine.core import optional
from ragspine.core.llm import LLMClient, LLMError, LLMUnavailable
from ragspine.core.security import jwt_encode, verify_password
from ragspine.rag import pipeline
from ragspine.web.deps import require_user


class LoginBody(BaseModel):
    username: str
    password: str


class ChatBody(BaseModel):
    q: str


class ChatCompletionsBody(BaseModel):
    messages: list[dict]
    model: str | None = None


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

    def _answer(query: str, user: str) -> dict:
        try:
            return pipeline.answer(spine, cfg, query, user, llm=LLMClient(cfg))
        except (LLMUnavailable, LLMError):
            return {"answer": "LLM trenutno nedostupan ili je vratio grešku.", "lane": "chat",
                    "confidence": 0, "sources": [], "cached": False}

    @app.post("/chat")
    def chat(body: ChatBody, user: str = Depends(require_user)):
        return _answer(body.q, user)

    @app.post("/v1/chat/completions")
    def chat_completions(body: ChatCompletionsBody, user: str = Depends(require_user)):
        user_msgs = [m for m in body.messages if m.get("role") == "user"]
        query = user_msgs[-1].get("content", "") if user_msgs else ""
        if not query:
            raise HTTPException(400, "no user message content")
        result = _answer(query, user)
        return {
            "choices": [{"message": {"role": "assistant", "content": result["answer"]}}],
            "model": cfg.llm_model or "ragspine",
            "usage": {},
        }

    return app
