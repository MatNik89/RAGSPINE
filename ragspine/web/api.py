import dataclasses
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ragspine.business import obveze
from ragspine.core import optional
from ragspine.core.llm import LLMClient, LLMError, LLMUnavailable
from ragspine.core.security import jwt_encode, verify_password
from ragspine.rag import pipeline
from ragspine.web import watchlist
from ragspine.web.deps import require_user
from ragspine.web.templates_obveze import render_obveze


class LoginBody(BaseModel):
    username: str
    password: str


class ChatBody(BaseModel):
    q: str


class ChatCompletionsBody(BaseModel):
    messages: list[dict]
    model: str | None = None


class WatchSourceBody(BaseModel):
    url: str
    category: str = ""
    client_id: int | None = None
    kind: str = "page"


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

    @app.post("/watchlist/run")
    def watchlist_run(user: str = Depends(require_user)):
        return [dataclasses.asdict(c) for c in watchlist.check_all(spine, cfg)]

    @app.get("/watchlist/sources")
    def watchlist_list_sources(user: str = Depends(require_user)):
        rows = spine.read().execute("SELECT * FROM watch_sources").fetchall()
        return [dict(r) for r in rows]

    @app.post("/watchlist/sources")
    def watchlist_add_source(body: WatchSourceBody, user: str = Depends(require_user)):
        sid = watchlist.add_source(spine, body.url, body.category, body.client_id, user, body.kind)
        return {"id": sid}

    @app.get("/obveze", response_class=HTMLResponse)
    def obveze_page(kind: str = "PDV", period: str | None = None,
                     user: str = Depends(require_user)):
        period = period or date.today().strftime("%Y-%m")
        obveze.ensure_period(spine, kind, period)
        rows = obveze.list_period(spine, kind, period)
        return render_obveze(kind, period, rows)

    @app.get("/obveze.json")
    def obveze_json(kind: str = "PDV", period: str | None = None,
                     user: str = Depends(require_user)):
        period = period or date.today().strftime("%Y-%m")
        return obveze.list_period(spine, kind, period)

    @app.post("/obveze/mark")
    async def obveze_mark(request: Request, user: str = Depends(require_user)):
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
        try:
            obligation_id = int(body["obligation_id"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "obligation_id required")
        sent = str(body.get("sent", "1")) not in ("0", "false", "False")
        obveze.mark_sent(spine, obligation_id, user, sent)
        if "application/json" in ctype:
            return {"obligation_id": obligation_id, "sent": sent}
        kind = body.get("kind", "PDV")
        period = body.get("period", date.today().strftime("%Y-%m"))
        return RedirectResponse(f"/obveze?kind={kind}&period={period}", status_code=303)

    return app
