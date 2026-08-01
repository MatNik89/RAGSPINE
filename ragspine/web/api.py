import dataclasses
from datetime import date
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from ragspine.business import auditlog
from ragspine.business import checklist
from ragspine.business import cjenik
from ragspine.business import dashboard
from ragspine.business import expiry as expiry_mod
from ragspine.business import feedback_learn
from ragspine.business import kalendar
from ragspine.business import knjizenje  # noqa: F401 — register knjizenje lane handler
from ragspine.business import monthly
from ragspine.business import nldate
from ragspine.business import notes
from ragspine.business import obveze
from ragspine.business import peer_compare
from ragspine.web import messaging
from ragspine.browser import agent as agent_mod
from ragspine.browser.bridge import Bridge
from ragspine.core import memory as memory_mod
from ragspine.core import optional
from ragspine.core.llm import LLMClient, LLMError, LLMUnavailable
from ragspine.core.security import hash_password, jwt_encode, verify_password
from ragspine.docs import doc_generator, ocr, vault
from ragspine.knowledge import features as features_mod
from ragspine.knowledge import patterns as patterns_mod
from ragspine.knowledge import translate as translate_mod
from ragspine.ops import doctor, health, model_recommender, nis2
from ragspine.rag import pipeline
from ragspine.rag import versioning
from ragspine.rag import sql_lane, graphrag  # noqa: F401 — register sql/graph lane handlers
from ragspine.web import learn  # noqa: F401 — register learn lane handler
from ragspine.web import watchlist
from ragspine.web import websearch  # noqa: F401 — register web lane handler
from ragspine.web.deps import COOKIE_NAME, require_user, require_user_web
from ragspine.web.templates_login import render_login
from ragspine.web.templates_obveze import render_obveze


class ChatBody(BaseModel):
    q: str
    fresh: bool = False


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


class NoteBody(BaseModel):
    client_id: int
    body: str


class MemoryBody(BaseModel):
    key: str
    value: str


class ReminderBody(BaseModel):
    body: str
    when: str


class MessagingSendBody(BaseModel):
    client_id: int
    subject: str
    body: str
    dry_run: bool = True


class MessagingCampaignBody(BaseModel):
    filter: str
    subject: str
    body: str
    dry_run: bool = True
    kind: str | None = None
    period: str | None = None
    days: int = 30


class ClientMessagingBody(BaseModel):
    consent: int
    channel: str = ""
    target: str = ""


class CjenikIzracunBody(BaseModel):
    client_id: int
    employees: int = 0
    extras: list[str] | None = None


class PausalBody(BaseModel):
    pausal_eur: float


class KnjizenjeBody(BaseModel):
    description: str


class KnjizenjeCorrectBody(BaseModel):
    description: str
    original_konto: str
    corrected_konto: str


class PeerBookingBody(BaseModel):
    description: str
    konto: str
    amount: float = 0


class OcrBody(BaseModel):
    path: str


class VaultScanBody(BaseModel):
    root: str
    ingest_new: bool = True


class TranslateBody(BaseModel):
    text: str
    target: str


class DocGenerateBody(BaseModel):
    doc_type: str
    client_id: int
    extra: dict | None = None


class FeatureBody(BaseModel):
    body: str
    priority: int = 3
    category: str = ""


class Nis2Body(BaseModel):
    control_id: str
    status: str


class KnowledgeStatusBody(BaseModel):
    status: str


class KnowledgeSupersedeBody(BaseModel):
    old_doc_id: int
    new_doc_id: int


class BrowserResultBody(BaseModel):
    cmd_id: str
    result: dict


class BrowserAgentBody(BaseModel):
    task: str
    url: str = ""


# ponytail: fixed dummy hash for login timing — run a real verify_password
# cost even when the username doesn't exist, so response latency doesn't
# leak which usernames are registered.
_DUMMY_PW_HASH = hash_password("nexus-dummy-pw-for-timing-only")


def create_app(spine, cfg) -> FastAPI:
    app = FastAPI()
    app.state.spine = spine
    app.state.cfg = cfg
    app.state.bridge = Bridge()

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
        if row is None:
            verify_password(password, _DUMMY_PW_HASH)  # constant-time-ish: keep latency ~equal
            raise HTTPException(401, "invalid credentials")
        if not verify_password(password, row["pw_hash"]):
            raise HTTPException(401, "invalid credentials")
        token = jwt_encode({"sub": username, "role": row["role"]}, cfg.jwt_secret)
        # ponytail: CSRF for POST /obveze/mark deferred — SameSite=Lax already blocks
        # cross-site POST-with-cookie on top-level navigation; a CSRF token is the
        # upgrade path if that stops being sufficient (e.g. subdomain untrusted).
        resp = JSONResponse({"token": token}) if is_json else RedirectResponse("/obveze", status_code=303)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=86400,
                         secure=cfg.https_only)
        return resp

    @app.get("/v1/models")
    def models(user: str = Depends(require_user)):
        return {"object": "list", "data": [{"id": cfg.llm_model or "ragspine", "object": "model"}]}

    def _answer(query: str, user: str, fresh: bool = False) -> dict:
        try:
            return pipeline.answer(spine, cfg, query, user, llm=LLMClient(cfg), fresh=fresh)
        except (LLMUnavailable, LLMError):
            return {"answer": "LLM trenutno nedostupan ili je vratio grešku.", "lane": "chat",
                    "confidence": 0, "sources": [], "cached": False}

    @app.post("/chat")
    def chat(body: ChatBody, user: str = Depends(require_user)):
        return _answer(body.q, user, fresh=body.fresh)

    @app.post("/v1/chat/completions")
    def chat_completions(body: ChatCompletionsBody, user: str = Depends(require_user)):
        user_msgs = [m for m in body.messages if m.get("role") == "user"]
        query = user_msgs[-1].get("content", "") if user_msgs else ""
        if not query:
            raise HTTPException(400, "no user message content")
        result = _answer(query, user, fresh=True)  # OpenAI-compat clients own their own history
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

    @app.post("/reminders")
    def reminders_add_nl(body: ReminderBody, user: str = Depends(require_user_web)):
        result = nldate.set_reminder_nl(spine, user, body.body, body.when)
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result

    @app.get("/checklist")
    def checklist_worst(user: str = Depends(require_user_web)):
        return checklist.worst_first(spine)

    @app.get("/notes")
    def notes_search(client_id: int | None = None, q: str | None = None,
                      user: str = Depends(require_user_web)):
        return [dict(r) for r in notes.search(spine, term=q, client_id=client_id)]

    @app.post("/notes")
    def notes_add(body: NoteBody, user: str = Depends(require_user_web)):
        note_id = notes.add(spine, body.client_id, user, body.body)
        return {"id": note_id}

    @app.get("/memory/hot")
    def memory_hot(limit: int = 10, user: str = Depends(require_user_web)):
        return memory_mod.hot_memories(spine, user, limit=limit)

    @app.post("/memory")
    def memory_write(body: MemoryBody, user: str = Depends(require_user_web)):
        memory_mod.write_memory(spine, user, body.key, body.value)
        return {"key": body.key}

    @app.get("/memory/{key}")
    def memory_get(key: str, user: str = Depends(require_user_web)):
        value = memory_mod.get_memory(spine, user, key)
        if value is None:
            raise HTTPException(404, "nema takvog zapisa")
        return {"key": key, "value": value}

    @app.post("/messaging/send")
    def messaging_send(body: MessagingSendBody, user: str = Depends(require_user_web)):
        return messaging.send_to_client(spine, cfg, body.client_id, body.subject, body.body,
                                         dry_run=body.dry_run)

    @app.post("/messaging/campaign")
    def messaging_campaign(body: MessagingCampaignBody, user: str = Depends(require_user_web)):
        kw = {"days": body.days}
        if body.kind is not None:
            kw["kind"] = body.kind
        if body.period is not None:
            kw["period"] = body.period
        try:
            return messaging.send_to_filter(spine, cfg, body.filter, body.subject, body.body,
                                             dry_run=body.dry_run, **kw)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/messaging/log")
    def messaging_log(client_id: int | None = None, user: str = Depends(require_user_web)):
        if client_id is not None:
            rows = spine.read().execute(
                "SELECT * FROM message_log WHERE client_id=? ORDER BY at DESC LIMIT 50", (client_id,)
            ).fetchall()
        else:
            rows = spine.read().execute(
                "SELECT * FROM message_log ORDER BY at DESC LIMIT 50"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/clients/{client_id}/messaging")
    def client_messaging_set(client_id: int, body: ClientMessagingBody,
                              user: str = Depends(require_user_web)):
        if body.consent not in (0, 1):
            raise HTTPException(400, "consent mora biti 0 ili 1")
        if spine.read().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone() is None:
            raise HTTPException(404, "nepoznat klijent")
        with spine.write() as c:
            c.execute(
                "UPDATE clients SET messaging_consent=?, messaging_channel=?, messaging_target=? WHERE id=?",
                (body.consent, body.channel, body.target, client_id),
            )
        return {"client_id": client_id, "consent": body.consent}

    @app.get("/cjenik")
    def cjenik_list(user: str = Depends(require_user_web)):
        return cjenik.price_list(spine)

    @app.post("/cjenik/izracun")
    def cjenik_izracun(body: CjenikIzracunBody, user: str = Depends(require_user_web)):
        try:
            return cjenik.izracunaj_cijenu(spine, body.client_id, employees=body.employees,
                                            extras=body.extras)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/cjenik/usporedba/{client_id}")
    def cjenik_usporedba(client_id: int, user: str = Depends(require_user_web)):
        try:
            return cjenik.usporedi_s_trzistem(spine, client_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/clients/{client_id}/pausal")
    def client_pausal_set(client_id: int, body: PausalBody, user: str = Depends(require_user_web)):
        if spine.read().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone() is None:
            raise HTTPException(404, "nepoznat klijent")
        with spine.write() as c:
            c.execute("UPDATE clients SET pausal_eur=? WHERE id=?", (body.pausal_eur, client_id))
        return {"client_id": client_id, "pausal_eur": body.pausal_eur}

    @app.get("/dashboard")
    def dashboard_stats(user: str = Depends(require_user_web)):
        return dashboard.stats(spine)

    @app.get("/models/recommend")
    def models_recommend(user: str = Depends(require_user_web)):
        return model_recommender.recommend()

    @app.get("/models/litellm")
    def models_litellm(user: str = Depends(require_user_web)):
        return Response(model_recommender.litellm_config(model_recommender.recommend()),
                         media_type="text/plain")

    @app.get("/monthly")
    def monthly_overview(period: str | None = None, user: str = Depends(require_user_web)):
        period = period or date.today().strftime("%Y-%m")
        ov = monthly.overview(spine, period)
        return {**ov, "text": monthly.format_overview(ov)}

    @app.post("/knjizenje")
    def knjizenje_suggest(body: KnjizenjeBody, user: str = Depends(require_user_web)):
        return knjizenje.suggest(spine, body.description)

    @app.post("/knjizenje/correct")
    def knjizenje_correct(body: KnjizenjeCorrectBody, user: str = Depends(require_user_web)):
        cid = feedback_learn.record_correction(spine, user, body.description,
                                                body.original_konto, body.corrected_konto)
        return {"id": cid, "learned": True}

    @app.post("/peer/booking")
    def peer_booking_record(body: PeerBookingBody, user: str = Depends(require_user_web)):
        bid = peer_compare.record_booking(spine, user, body.description, body.konto,
                                           amount=body.amount)
        return {"id": bid}

    @app.get("/peer/disagreements")
    def peer_disagreements(days: int = 30, user: str = Depends(require_user_web)):
        return {"disagreements": peer_compare.find_disagreements(spine, days=days),
                "summary": peer_compare.peer_summary(spine, days=days)}

    @app.post("/ocr")
    def ocr_run(body: OcrBody, user: str = Depends(require_user_web)):
        try:
            return ocr.ocr_pdf(spine, cfg, body.path)
        except ocr.OCRUnavailable as e:
            raise HTTPException(503, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/vault/scan")
    def vault_scan_run(body: VaultScanBody, user: str = Depends(require_user_web)):
        try:
            root = vault.resolve_scope(cfg, body.root)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return vault.scan_directory(spine, root, ingest_new=body.ingest_new)

    @app.get("/vault/status")
    def vault_status_get(user: str = Depends(require_user_web)):
        return vault.vault_status(spine)

    @app.post("/translate")
    def translate_text(body: TranslateBody, user: str = Depends(require_user_web)):
        try:
            text = translate_mod.translate(LLMClient(cfg), body.text, body.target)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except (LLMUnavailable, LLMError) as e:
            # ponytail: don't surface provider error bodies to the client — could
            # contain upstream noise/internals. Detail stays server-side only.
            raise HTTPException(503, "Greška LLM providera.") from e
        return {"translation": text}

    @app.get("/doc/templates")
    def doc_templates(user: str = Depends(require_user_web)):
        return list(doc_generator.TEMPLATES)

    @app.post("/doc/generate")
    def doc_generate(body: DocGenerateBody, user: str = Depends(require_user_web)):
        try:
            result = doc_generator.generate_from_client(
                spine, body.doc_type, body.client_id, extra=body.extra)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        out = {"text": result["text"],
               "gate": {"ok": result["gate"].ok, "missing": result["gate"].missing}}
        if "warning" in result:
            out["warning"] = result["warning"]
        return out

    @app.get("/features")
    def features_list(user: str = Depends(require_user_web)):
        return [dict(r) for r in features_mod.list_open(spine)]

    @app.post("/features")
    def features_add(body: FeatureBody, user: str = Depends(require_user_web)):
        fid = features_mod.add(spine, user, body.body, body.priority, body.category)
        return {"id": fid}

    @app.get("/patterns")
    def patterns_detect(user: str = Depends(require_user_web)):
        return patterns_mod.detect(spine)

    @app.get("/audit")
    def audit_search(client: str | None = None, user: str | None = None,
                      action: str | None = None, _auth: str = Depends(require_user_web)):
        rows = auditlog.search(spine, client=client, user=user, action=action)
        return [dict(r) for r in rows]

    @app.get("/doctor")
    def doctor_run(user: str = Depends(require_user_web)):
        return doctor.run(cfg)

    @app.get("/health/full")
    def health_full(user: str = Depends(require_user_web)):
        return health.check(spine, cfg)

    @app.get("/nis2")
    def nis2_report(user: str = Depends(require_user_web)):
        return nis2.report(spine)

    @app.post("/nis2")
    def nis2_set(body: Nis2Body, user: str = Depends(require_user_web)):
        nis2.set_status(spine, body.control_id, body.status)
        return {"id": body.control_id, "status": body.status}

    @app.post("/knowledge/{doc_id}/status")
    def knowledge_set_status(doc_id: int, body: KnowledgeStatusBody, user: str = Depends(require_user_web)):
        try:
            versioning.set_status(spine, doc_id, body.status, user=user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"doc_id": doc_id, "status": body.status}

    @app.post("/knowledge/supersede")
    def knowledge_supersede(body: KnowledgeSupersedeBody, user: str = Depends(require_user_web)):
        try:
            versioning.supersede(spine, body.old_doc_id, body.new_doc_id, user=user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"old_doc_id": body.old_doc_id, "new_doc_id": body.new_doc_id}

    @app.get("/knowledge/{doc_id}/versions")
    def knowledge_versions(doc_id: int, user: str = Depends(require_user_web)):
        try:
            return versioning.version_history(spine, doc_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/knowledge/{doc_id}/promote")
    def knowledge_promote(doc_id: int, user: str = Depends(require_user_web)):
        try:
            versioning.promote_draft(spine, doc_id, user=user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"doc_id": doc_id, "status": "active"}

    @app.get("/browser/cmd")
    def browser_cmd(request: Request, user: str = Depends(require_user_web)):
        bridge = request.app.state.bridge
        cmd = bridge.next_cmd(timeout=bridge.cmd_timeout)
        if cmd is None:
            return Response(status_code=204)
        return cmd

    @app.post("/browser/result")
    def browser_result(request: Request, body: BrowserResultBody, user: str = Depends(require_user_web)):
        request.app.state.bridge.post_result(body.cmd_id, body.result)
        return {"ok": True}

    @app.post("/browser/run")
    async def browser_run(request: Request, user: str = Depends(require_user_web)):
        action = await request.json()
        bridge = request.app.state.bridge
        cmd_id = bridge.enqueue(action)
        result = bridge.wait_result(cmd_id, timeout=bridge.run_timeout)
        if result is None:
            return JSONResponse({"error": "timeout"}, status_code=504)
        return result

    @app.get("/browser/status")
    def browser_status(request: Request, user: str = Depends(require_user_web)):
        return {"pending": request.app.state.bridge.pending()}

    @app.post("/browser/agent")
    def browser_agent(body: BrowserAgentBody, user: str = Depends(require_user_web)):
        return agent_mod.run_task(cfg, body.task, body.url)

    return app
