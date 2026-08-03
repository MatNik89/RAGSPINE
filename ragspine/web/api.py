import base64
import binascii
import dataclasses
import re
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
from ragspine.business import folders as folders_mod
from ragspine.business import kalendar
from ragspine.business import karton as karton_mod
from ragspine.business import knjizenje  # noqa: F401 — register knjizenje lane handler
from ragspine.business import model_settings
from ragspine.business import monthly
from ragspine.business import nldate
from ragspine.business import notes
from ragspine.business import obveze
from ragspine.business import onboarding
from ragspine.business import peer_compare
from ragspine.business import sop as sop_mod
from ragspine.business import sop_images
from ragspine.business import tenancy
from ragspine.business.acl import ROLE_RANK, Actor
from ragspine.web import messaging
from ragspine.web import static as static_mod
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
from ragspine.web.deps import (COOKIE_NAME, require_actor, require_actor_web, require_user,
                               require_user_web)
from ragspine.web.templates_login import render_login
from ragspine.web.templates_mape import mape_page
from ragspine.web.templates_model import model_page
from ragspine.web.templates_obveze import obveze_none_page, obveze_types_page, render_obveze
from ragspine.web.templates_ui import (chat_page, dashboard_page, dokumenti_page, klijent_page,
                                        klijenti_page, obavijesti_page, postavke_page, upute_page)


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


class ClientCreateBody(BaseModel):
    name: str
    oib: str | None = None
    email: str = ""
    phone: str = ""
    industry: str = ""
    pdv_status: str = ""
    pausal_eur: float = 0
    has_employees: int = 0
    pdv_freq: str = "monthly"
    regime: str = ""


class ClientDocumentBody(BaseModel):
    filename: str
    data_base64: str


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


class ObligationTypeBody(BaseModel):
    kind: str
    label: str = ""
    rule: str = ""
    frequency: str = "monthly"
    applies_to: str = "all_active"
    active: int = 1
    sort: int = 100
    description: str = ""


class ModelSettingsBody(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    embed_model: str = ""
    ollama_url: str = ""


class FolderBody(BaseModel):
    path: str
    role: str = "ostalo"
    label: str = ""


class FolderUpdateBody(BaseModel):
    role: str | None = None
    label: str | None = None
    enabled: int | None = None


class ClientObligationsBody(BaseModel):
    # None = "ne diraj" (parcijalni POST ne smije obrisati ostala polja)
    has_employees: int | None = None
    pdv_freq: str | None = None
    regime: str | None = None
    manual_kinds: list[str] | None = None


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


class SopBody(BaseModel):
    title: str
    category: str
    content: str
    client_id: int | None = None


class SopRejectBody(BaseModel):
    reason: str = ""


class SopImageBody(BaseModel):
    filename: str
    data_base64: str
    caption: str = ""


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


# period is echoed back into an inline <script> on /obveze (as PERIOD) and
# into a redirect URL on /obveze/mark — a free-form string here is a reflected
# XSS / header-injection surface, so every entry point validates the shape
# strictly (YYYY-MM) before it goes anywhere near a response.
_PERIOD_RE = re.compile(r"\d{4}-\d{2}")


def _require_valid_period(period: str) -> None:
    if not _PERIOD_RE.fullmatch(period):
        raise HTTPException(400, "neispravan period")


# ponytail: fixed dummy hash for login timing — run a real verify_password
# cost even when the username doesn't exist, so response latency doesn't
# leak which usernames are registered.
_DUMMY_PW_HASH = hash_password("nexus-dummy-pw-for-timing-only")


def create_app(spine, cfg) -> FastAPI:
    app = FastAPI()
    app.state.spine = spine
    app.state.cfg = cfg
    app.state.bridge = Bridge()
    tenancy.backfill_org(spine)  # idempotentno: postojeći dokumenti/znanje → default org
    app.include_router(static_mod.router)

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
            "SELECT id, pw_hash, role FROM users WHERE username=?", (username,)
        ).fetchone()
        if row is None:
            verify_password(password, _DUMMY_PW_HASH)  # constant-time-ish: keep latency ~equal
            raise HTTPException(401, "invalid credentials")
        if not verify_password(password, row["pw_hash"]):
            raise HTTPException(401, "invalid credentials")
        # uid+org_id su pokazivači za Actor lookup; org-uloga se NE stavlja u
        # token (čita se svježa iz memberships na svakom zahtjevu).
        org_id, _ = tenancy.resolve_login_org(spine, row["id"], row["role"])
        token = jwt_encode({"sub": username, "role": row["role"],
                            "uid": row["id"], "org_id": org_id}, cfg.jwt_secret)
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

    def _require_admin(actor: Actor) -> Actor:
        if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["admin"]:
            raise HTTPException(403, "potrebna admin uloga")
        return actor

    @app.get("/org")
    def org_info(actor: Actor = Depends(require_actor_web)):
        org = spine.read().execute("SELECT id, name FROM orgs WHERE id=?",
                                   (actor.org_id,)).fetchone()
        return {"org": dict(org) if org else None, "role": actor.role,
                "members": tenancy.list_members(spine, actor.org_id)}

    def _answer(query: str, actor: Actor, fresh: bool = False) -> dict:
        try:
            return pipeline.answer(spine, cfg, query, actor.username,
                                   llm=LLMClient(model_settings.apply(spine, cfg)), fresh=fresh,
                                   actor=actor)
        except (LLMUnavailable, LLMError):
            return {"answer": "LLM trenutno nedostupan ili je vratio grešku.", "lane": "chat",
                    "confidence": 0, "sources": [], "cached": False}

    @app.post("/chat")
    def chat(body: ChatBody, actor: Actor = Depends(require_actor)):
        return _answer(body.q, actor, fresh=body.fresh)

    @app.post("/v1/chat/completions")
    def chat_completions(body: ChatCompletionsBody, actor: Actor = Depends(require_actor)):
        user_msgs = [m for m in body.messages if m.get("role") == "user"]
        query = user_msgs[-1].get("content", "") if user_msgs else ""
        if not query:
            raise HTTPException(400, "no user message content")
        result = _answer(query, actor, fresh=True)  # OpenAI-compat clients own their own history
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

    @app.get("/", response_class=HTMLResponse)
    def ui_home(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return dashboard_page()

    @app.get("/dashboard.json")
    def dashboard_json(user: str = Depends(require_user_web)):
        return dashboard.home_data(spine)

    @app.get("/ui/chat", response_class=HTMLResponse)
    def ui_chat(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return chat_page()

    @app.get("/ui/upute", response_class=HTMLResponse)
    def ui_upute(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return upute_page(sop_mod.list_pending(spine))

    @app.get("/ui/klijenti", response_class=HTMLResponse)
    def ui_klijenti(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return klijenti_page()

    @app.get("/ui/klijent/{client_id}", response_class=HTMLResponse)
    def ui_klijent(request: Request, client_id: int):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return klijent_page(client_id)

    @app.get("/ui/obavijesti", response_class=HTMLResponse)
    def ui_obavijesti(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return obavijesti_page()

    @app.get("/ui/obveze-tipovi", response_class=HTMLResponse)
    def ui_obveze_tipovi(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return obveze_types_page()

    @app.get("/ui/postavke", response_class=HTMLResponse)
    def ui_postavke(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return postavke_page()

    @app.get("/ui/mape", response_class=HTMLResponse)
    def ui_mape(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return mape_page()

    @app.get("/ui/model", response_class=HTMLResponse)
    def ui_model(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return model_page()

    @app.get("/model")
    def model_get(user: str = Depends(require_user_web)):
        return model_settings.get(spine)

    @app.post("/model")
    def model_save(body: ModelSettingsBody, user: str = Depends(require_user_web)):
        try:
            return model_settings.save(spine, body.provider, body.model, body.base_url,
                                       body.api_key, body.embed_model, body.ollama_url, user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/model/test")
    def model_test(user: str = Depends(require_user_web)):
        return model_settings.test_connection(spine, cfg)

    @app.get("/folders/browse")
    def folders_browse(path: str | None = None, user: str = Depends(require_user_web)):
        try:
            return folders_mod.browse(cfg, path)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/folders")
    def folders_list(user: str = Depends(require_user_web)):
        return folders_mod.list_folders(spine)

    @app.post("/folders/sync")
    def folders_sync_now(user: str = Depends(require_user_web)):
        from ragspine.business import folder_sync
        return folder_sync.sync_all(spine, cfg)

    @app.post("/folders")
    def folders_register(body: FolderBody, user: str = Depends(require_user_web)):
        try:
            return folders_mod.register(spine, cfg, body.path, body.role, body.label, user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/folders/{folder_id}")
    def folders_update(folder_id: int, body: FolderUpdateBody,
                       user: str = Depends(require_user_web)):
        try:
            return folders_mod.update(spine, folder_id, role=body.role, label=body.label,
                                      enabled=body.enabled, user=user)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.delete("/folders/{folder_id}")
    def folders_delete(folder_id: int, user: str = Depends(require_user_web)):
        try:
            folders_mod.remove(spine, folder_id, user)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"id": folder_id, "removed": True}

    @app.get("/ui/dokumenti", response_class=HTMLResponse)
    def ui_dokumenti(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return dokumenti_page()

    @app.get("/notifications.json")
    def notifications_json(user: str = Depends(require_user_web)):
        rows = spine.read().execute(
            "SELECT id, kind, body, client_id, seen, at FROM notifications ORDER BY at DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/notifications/{notif_id}/seen")
    def notifications_mark_seen(notif_id: int, user: str = Depends(require_user_web)):
        with spine.write() as c:
            if c.execute("SELECT 1 FROM notifications WHERE id=?", (notif_id,)).fetchone() is None:
                raise HTTPException(404, "nepoznata obavijest")
            c.execute("UPDATE notifications SET seen=1 WHERE id=?", (notif_id,))
        return {"id": notif_id, "seen": True}

    @app.get("/obveze", response_class=HTMLResponse)
    def obveze_page(request: Request, kind: str | None = None, period: str | None = None):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        tabs = [(t["kind"], t["label"]) for t in obveze.list_types(spine, active_only=True)]
        active = [k for k, _ in tabs]
        if not active:
            return HTMLResponse(obveze_none_page())
        kind = kind or active[0]
        if kind not in active:
            raise HTTPException(400, f"nepoznat kind: {kind!r}")
        period = period or date.today().strftime("%Y-%m")
        _require_valid_period(period)
        obveze.ensure_period(spine, kind, period)
        rows = obveze.list_period(spine, kind, period)
        return render_obveze(kind, period, rows, tabs)

    @app.get("/obveze.json")
    def obveze_json(kind: str = "PDV", period: str | None = None,
                     user: str = Depends(require_user_web)):
        if obveze.get_type(spine, kind) is None:
            raise HTTPException(400, f"nepoznat kind: {kind!r}")
        period = period or date.today().strftime("%Y-%m")
        _require_valid_period(period)
        return obveze.list_period(spine, kind, period)

    @app.get("/obveze/tipovi")
    def obveze_types_list(user: str = Depends(require_user_web)):
        return obveze.list_types(spine)

    @app.post("/obveze/tipovi")
    def obveze_types_upsert(body: ObligationTypeBody, user: str = Depends(require_user_web)):
        try:
            kind = obveze.upsert_type(
                spine, body.kind, body.label, body.rule, body.frequency,
                body.applies_to, active=bool(body.active), sort=body.sort,
                description=body.description, user=user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"kind": kind}

    @app.get("/clients/{client_id}/obveze-postavke")
    def client_obligations_get(client_id: int, user: str = Depends(require_user_web)):
        row = spine.read().execute(
            "SELECT has_employees, pdv_freq, regime FROM clients WHERE id=?", (client_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznat klijent")
        available = [{"kind": t["kind"], "label": t["label"]}
                     for t in obveze.list_types(spine, active_only=True)
                     if t["applies_to"] == "manual"]
        return {
            "has_employees": row["has_employees"] or 0,
            "pdv_freq": row["pdv_freq"] or "monthly",
            "regime": row["regime"] or "",
            "manual_kinds": obveze.client_types(spine, client_id),
            "available_manual": available,
        }

    @app.post("/clients/{client_id}/obveze-postavke")
    def client_obligations_set(client_id: int, body: ClientObligationsBody,
                                user: str = Depends(require_user_web)):
        if body.pdv_freq is not None and body.pdv_freq not in ("monthly", "quarterly"):
            raise HTTPException(400, "pdv_freq mora biti monthly ili quarterly")
        if body.regime is not None and body.regime not in obveze.REGIMES:
            raise HTTPException(400, f"nepoznat porezni sustav: {body.regime!r}")
        if spine.read().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone() is None:
            raise HTTPException(404, "nepoznat klijent")
        # samo proslijeđena polja se mijenjaju; izostavljena ostaju netaknuta
        sets, vals = [], []
        if body.has_employees is not None:
            sets.append("has_employees=?"); vals.append(1 if body.has_employees else 0)
        if body.pdv_freq is not None:
            sets.append("pdv_freq=?"); vals.append(body.pdv_freq)
        if body.regime is not None:
            sets.append("regime=?"); vals.append(body.regime)
        if sets:
            with spine.write() as c:
                c.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id=?", (*vals, client_id))
        if body.manual_kinds is not None:
            obveze.set_client_types(spine, client_id, body.manual_kinds, user=user)
        row = spine.read().execute(
            "SELECT has_employees, pdv_freq, regime FROM clients WHERE id=?", (client_id,)).fetchone()
        return {"client_id": client_id, "has_employees": row["has_employees"] or 0,
                "pdv_freq": row["pdv_freq"] or "monthly", "regime": row["regime"] or "",
                "manual_kinds": obveze.client_types(spine, client_id)}

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
        if obveze.get_type(spine, kind) is None:
            raise HTTPException(400, f"nepoznat kind: {kind!r}")
        _require_valid_period(period)
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

    @app.post("/clients")
    def client_create(body: ClientCreateBody, user: str = Depends(require_user_web)):
        try:
            result = onboarding.create_client(spine, cfg, body.model_dump(), user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": result["id"], "nas_folder": result["nas_folder"]}

    @app.get("/clients")
    def clients_list(user: str = Depends(require_user_web)):
        rows = spine.read().execute(
            "SELECT id, name, oib, pdv_status, industry, regime, active FROM clients ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/clients/{client_id}")
    def client_get(client_id: int, user: str = Depends(require_user_web)):
        row = spine.read().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznat klijent")
        return dict(row)

    @app.post("/clients/{client_id}/document")
    def client_document_add(client_id: int, body: ClientDocumentBody,
                             user: str = Depends(require_user_web)):
        if spine.read().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone() is None:
            raise HTTPException(404, "nepoznat klijent")
        try:
            data = base64.b64decode(body.data_base64, validate=True)
        except binascii.Error as e:
            raise HTTPException(400, "neispravan base64") from e
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "dokument prevelik (max 25MB)")
        try:
            return onboarding.add_document(spine, cfg, client_id, body.filename, data, owner=user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/clients/{client_id}/documents")
    def client_documents_list(client_id: int, user: str = Depends(require_user_web)):
        try:
            return onboarding.list_documents(spine, cfg, client_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/clients/{client_id}/karton.json")
    def client_karton(client_id: int, user: str = Depends(require_user_web)):
        try:
            return karton_mod.karton_data(spine, cfg, client_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/clients/{client_id}/messaging")
    def client_messaging_set(client_id: int, body: ClientMessagingBody,
                              user: str = Depends(require_user_web)):
        if body.consent not in (0, 1):
            raise HTTPException(400, "consent mora biti 0 ili 1")
        if body.target and not messaging._target_scheme_ok(body.target):
            raise HTTPException(400, "nedozvoljen kanal")
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
            text = translate_mod.translate(LLMClient(model_settings.apply(spine, cfg)), body.text, body.target)
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

    @app.post("/sop")
    def sop_create(body: SopBody, user: str = Depends(require_user_web)):
        sop_id = sop_mod.create_sop(spine, user, body.title, body.category, body.content,
                                     client_id=body.client_id)
        return {"id": sop_id}

    @app.post("/sop/{sop_id}/submit")
    def sop_submit(sop_id: int, user: str = Depends(require_user_web)):
        try:
            sop_mod.submit_draft(spine, sop_id, user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": sop_id, "status": "submitted"}

    # ponytail: any authenticated user can approve for now (single small
    # office) — upgrade path is a role check (only 'voditelj'/admin approves)
    # once users.role is actually enforced elsewhere.
    @app.post("/sop/{sop_id}/approve")
    def sop_approve(sop_id: int, user: str = Depends(require_user_web)):
        try:
            doc_id = sop_mod.approve_draft(spine, sop_id, user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": sop_id, "status": "approved", "doc_id": doc_id}

    @app.post("/sop/{sop_id}/reject")
    def sop_reject(sop_id: int, body: SopRejectBody, user: str = Depends(require_user_web)):
        try:
            sop_mod.reject_draft(spine, sop_id, user, reason=body.reason)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": sop_id, "status": "rejected"}

    @app.get("/sop/pending")
    def sop_pending(user: str = Depends(require_user_web)):
        return {"items": sop_mod.list_pending(spine), "summary": sop_mod.editorial_summary(spine)}

    @app.get("/sop/{sop_id}")
    def sop_get(sop_id: int, user: str = Depends(require_user_web)):
        row = sop_mod.get_sop(spine, sop_id)
        if row is None:
            raise HTTPException(404, "nepoznat SOP")
        return row

    @app.post("/sop/{sop_id}/image")
    def sop_image_add(sop_id: int, body: SopImageBody, user: str = Depends(require_user_web)):
        try:
            data = base64.b64decode(body.data_base64, validate=True)
        except binascii.Error as e:
            raise HTTPException(400, "neispravan base64") from e
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(400, "slika prevelika (max 10MB)")
        try:
            result = sop_images.add_image(spine, cfg, sop_id, body.filename, data,
                                           caption=body.caption)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": result["id"], "ocr_text_len": len(result["ocr_text"])}

    @app.get("/sop/{sop_id}/images")
    def sop_image_list(sop_id: int, user: str = Depends(require_user_web)):
        return sop_images.list_images(spine, sop_id)

    @app.get("/sop/image/{image_id}")
    def sop_image_get(image_id: int, user: str = Depends(require_user_web)):
        result = sop_images.image_bytes(spine, cfg, image_id)
        if result is None:
            raise HTTPException(404, "slika nije pronađena")
        data, mime = result
        return Response(content=data, media_type=mime)

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
