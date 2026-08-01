import dataclasses
from datetime import date
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from ragspine.business import expiry as expiry_mod
from ragspine.business import kalendar
from ragspine.business import obveze
from ragspine.core import optional
from ragspine.core.llm import LLMClient, LLMError, LLMUnavailable
from ragspine.core.security import jwt_encode, verify_password
from ragspine.rag import pipeline
from ragspine.web import watchlist
from ragspine.web.deps import COOKIE_NAME, require_user, require_user_web
from ragspine.web.templates_login import render_login
from ragspine.web.templates_obveze import render_obveze


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


class ExpiryBody(BaseModel):
    client_id: int
    kind: str
    label: str
    expires: str


def create_app(spine, cfg) -> FastAPI:
    app = FastAPI()
    app.state.spine = spine
    app.state.cfg = cfg

    @app.get("/health")
    def health():
        return {"status": "ok", "missing": optional.missing()}

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        return render_login()

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.post("/auth/login")
    async def login(request: Request):
        ctype = request.headers.get("content-type", "")
        is_json = "application/json" in ctype
        try:
            body = await request.json() if is_json else dict(await request.form())
            username, password = body["username"], body["password"]
        except Exception:
            raise HTTPException(400, "neispravan zahtjev")
        if not username or not password:
            raise HTTPException(400, "neispravan zahtjev")
        row = spine.read().execute(
            "SELECT pw_hash, role FROM users WHERE username=?", (username,)
        ).fetchone()
        if row is None or not verify_password(password, row["pw_hash"]):
            raise HTTPException(401, "invalid credentials")
        token = jwt_encode({"sub": username, "role": row["role"]}, cfg.jwt_secret)
        # ponytail: CSRF for POST /obveze/mark deferred — SameSite=Lax already blocks
        # cross-site POST-with-cookie on top-level navigation; a CSRF token is the
        # upgrade path if that stops being sufficient (e.g. subdomain untrusted).
        resp = JSONResponse({"token": token}) if is_json else RedirectResponse("/obveze", status_code=303)
        # ponytail: secure=True omitted — v1 is LAN/plain-HTTP. Upgrade path: set
        # secure=True (or drive it from cfg, e.g. cfg.https_only) once served over HTTPS.
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=86400)
        return resp

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
    def obveze_page(request: Request, kind: str = "PDV", period: str | None = None):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        if kind not in obveze.KINDS:
            raise HTTPException(400, f"nepoznat kind: {kind!r}")
        period = period or date.today().strftime("%Y-%m")
        obveze.ensure_period(spine, kind, period)
        rows = obveze.list_period(spine, kind, period)
        return render_obveze(kind, period, rows)

    @app.get("/obveze.json")
    def obveze_json(kind: str = "PDV", period: str | None = None,
                     user: str = Depends(require_user_web)):
        if kind not in obveze.KINDS:
            raise HTTPException(400, f"nepoznat kind: {kind!r}")
        period = period or date.today().strftime("%Y-%m")
        return obveze.list_period(spine, kind, period)

    @app.post("/obveze/mark")
    async def obveze_mark(request: Request, user: str = Depends(require_user_web)):
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
        kind = body.get("kind", "PDV")
        period = body.get("period", date.today().strftime("%Y-%m"))
        if kind not in obveze.KINDS:
            raise HTTPException(400, f"nepoznat kind: {kind!r}")
        sent = str(body.get("sent", "1")) not in ("0", "false", "False")
        try:
            obveze.mark_sent(spine, obligation_id, user, sent)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        if "application/json" in ctype:
            return {"obligation_id": obligation_id, "sent": sent}
        return RedirectResponse(f"/obveze?kind={quote(kind)}&period={quote(period)}", status_code=303)

    @app.get("/kalendar")
    def kalendar_upcoming(days: int = 14, user: str = Depends(require_user_web)):
        return [dict(r) for r in kalendar.upcoming(spine, days)]

    @app.get("/expiry")
    def expiry_expiring(days: int = 60, user: str = Depends(require_user_web)):
        return [dict(r) for r in expiry_mod.expiring(spine, days)]

    @app.post("/expiry")
    def expiry_add(body: ExpiryBody, user: str = Depends(require_user_web)):
        item_id = expiry_mod.add(spine, body.client_id, body.kind, body.label, body.expires)
        return {"id": item_id}

    return app
