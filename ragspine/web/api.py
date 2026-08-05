import base64
import binascii
import dataclasses
import os
import re
from datetime import date
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ragspine.business import auditlog
from ragspine.business import checklist
from ragspine.business import cjenik
from ragspine.business import client_visibility
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
from ragspine.business import doc_registry, obveze
from ragspine.business import onboarding
from ragspine.business import peer_compare
from ragspine.business import sop as sop_mod
from ragspine.business import sop_images
from ragspine.business import tenancy
from ragspine.business.acl import ROLE_RANK, VISIBILITIES, Actor, Asset, can
from ragspine.knowledge import skills as skills_mod
from ragspine.knowledge import wiki as wiki_mod
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
from ragspine.web.ratelimit import RateLimiter
from ragspine.web import watchlist
from ragspine.web import websearch  # noqa: F401 — register web lane handler
from ragspine.web.deps import (COOKIE_NAME, add_user, require_actor, require_actor_web, require_user,
                               require_user_web)
from ragspine.web.templates_login import render_login
from ragspine.web.templates_mape import mape_page
from ragspine.web.templates_model import model_page
from ragspine.web.templates_obveze import obveze_none_page, obveze_types_page, render_obveze
from ragspine.web.templates_org import (org_page, radnici_page, skills_page,
                                        wiki_page as wiki_page_ui)
from ragspine.web.templates_ui import (chat_page, dashboard_page, dokumenti_page, klijent_page,
                                        klijenti_page, obavijesti_page, postavke_page, upute_page)


class ChatBody(BaseModel):
    q: str
    fresh: bool = False


class ChatCompletionsBody(BaseModel):
    messages: list[dict]
    model: str | None = None


class OrgMemberBody(BaseModel):
    username: str
    role: str = "member"


class OrgRoleBody(BaseModel):
    role: str


class WikiLockBody(BaseModel):
    locked: bool


class WorkerVisibilityBody(BaseModel):
    sees_all: bool
    client_ids: list[int] = []


class FolderNoteBody(BaseModel):
    folder_id: int | None = None
    body: str


class ConnectorTestBody(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    config: dict = Field(default_factory=dict)


class ConnectorCreateBody(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    config: dict = Field(default_factory=dict)


class ConnectorStatusBody(BaseModel):
    status: str = Field(min_length=1, max_length=20)


class SetupOwnerBody(BaseModel):
    # min 8 = osnovna jačina; max 128 = štiti PBKDF2 od golemog inputa (Codex)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class DiscoverCommitBody(BaseModel):
    folder_id: int
    items: list[dict]


class SkillBody(BaseModel):
    name: str
    description: str = ""
    trigger: str = ""
    steps: str = ""
    validation: str = ""
    visibility: str = "org"


class SkillUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger: str | None = None
    steps: str | None = None
    validation: str | None = None
    visibility: str | None = None


class SkillStatusBody(BaseModel):
    status: str


class KeywordsBody(BaseModel):
    keywords: list[str] = []


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
    legal_form: str = ""
    doc_types: list[str] = []


class ClientAssistBody(BaseModel):
    name: str = ""
    oib: str = ""
    legal_form: str = ""
    regime: str = ""
    pdv_status: str = ""
    has_employees: int = 0
    pausal_eur: float = 0


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


class DeviceBody(BaseModel):
    kind: str
    name: str
    url: str


class PrintBody(BaseModel):
    doc_id: int


class ArchTemplateBody(BaseModel):
    office: list[str] | None = None
    client_subdirs: list[str] | None = None


class ExtractBody(BaseModel):
    doc_id: int
    doc_type: str
    client_id: int | None = None


class DocTypeFieldBody(BaseModel):
    key: str
    label: str = ""
    kind: str = "text"
    expiry: bool = False


class DocTypeBody(BaseModel):
    key: str
    label: str = ""
    fields: list[DocTypeFieldBody] = []
    active: int = 1
    sort: int = 100


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
    # /docs + /redoc + /openapi.json ugašeni: Swagger UI vuče JS/CSS s CDN-a (pada
    # pod strogim CSP-om), a interaktivni API explorer je bespotrebna neautenti-
    # cirana enumeracija površine na produkcijskom LAN-u.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.spine = spine
    app.state.cfg = cfg
    app.state.bridge = Bridge()
    tenancy.backfill_org(spine)  # idempotentno: postojeći dokumenti/znanje → default org
    limiter = RateLimiter()
    _LOGIN_PER_MIN, _LOGIN_IP_PER_MIN, _CHAT_PER_MIN = 10, 30, 30  # ponytail: config knob tek kad zatreba
    from ragspine.business.connector_adapters import register_builtin
    register_builtin()  # registrira mail/telegram/whatsapp tipove (idempotentno)

    # Sigurnosna zaglavlja (defense-in-depth): clickjacking, MIME-sniff, referrer
    # leak, base-tag hijack. CSP dopušta 'unsafe-inline' jer je UI inline-script,
    # ali bez 'unsafe-eval' (nema eval/Function) i bez vanjskih izvora (LAN/offline).
    _CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'")

    _MAX_BODY = 64 * 1024 * 1024  # 64MB: iznad 25MB base64 uploada (~34MB), ispod DoS-a

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        # pre-decode gate: Starlette bufferira cijelo tijelo prije handlera, pa
        # 1GB JSON = 1.75GB RAM prije 400 — odbij po Content-Lengthu odmah.
        # ponytail: hvata samo zahtjeve s Content-Lengthom; chunked/streaming bez
        # njega prolazi — pravi hard-limit je na reverse-proxyju (deploy doc), ovo
        # je jeftin prvi filter za tipičan JSON-bomb.
        clen = request.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > _MAX_BODY:
            return JSONResponse({"detail": "tijelo zahtjeva preveliko", "max_bytes": _MAX_BODY},
                                status_code=413)
        # first-run gatekeeper: dok ne postoji nijedan korisnik, navigacija ide
        # na wizard (/ui/setup). Uzor Open WebUI (has_users()==False).
        from ragspine.web import firstrun
        if request.method == "GET" and firstrun._redirect_target(request.url.path) \
                and firstrun.needs_onboarding(spine):
            return RedirectResponse("/ui/setup", status_code=303)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = _CSP
        resp.headers["Server"] = "RAGSPINE"
        if cfg.https_only:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

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
        ip = request.client.host if request.client else "?"
        # IP-only limiter uz per-user: bez njega napadač churnom junk-usernameova
        # napuni limiter preko capa i evicta žrtvin blokirani bucket (Codex r3).
        # 30/min po IP-u ograničava i stopu stvaranja novih ključeva.
        if not limiter.allow(f"login-ip:{ip}", _LOGIN_IP_PER_MIN):
            raise HTTPException(429, "previše pokušaja prijave s ove adrese — pričekajte minutu")
        if not limiter.allow(f"login:{ip}:{username}", _LOGIN_PER_MIN):
            raise HTTPException(429, "previše pokušaja prijave — pričekajte minutu")
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

    @app.post("/org/members")
    def org_add_member(body: OrgMemberBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        if body.role not in ROLE_RANK:
            raise HTTPException(400, "nepoznata uloga")
        if body.role == "owner" and actor.role != "owner":
            raise HTTPException(403, "samo owner dodjeljuje owner ulogu")
        u = spine.read().execute("SELECT id FROM users WHERE username=?",
                                 (body.username,)).fetchone()
        if u is None:
            raise HTTPException(404, "nepoznat korisnik — prvo mu kreiraj račun")
        # add je insert-only: postojećem članu se uloga mijenja ISKLJUČIVO kroz
        # /org/members/{id}/role (koji nosi owner-only + last-owner guardove) —
        # inače bi admin upsertom "dodavanja" mogao degradirati ownera.
        if tenancy.role_of(spine, actor.org_id, u["id"]) is not None:
            raise HTTPException(409, "već je član — ulogu mijenjaj kroz promjenu uloge")
        tenancy.add_member(spine, actor.org_id, u["id"], body.role, user=actor.username)
        return {"ok": True}

    @app.post("/org/members/{member_id}/role")
    def org_set_role(member_id: int, body: OrgRoleBody,
                     actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        if body.role not in ROLE_RANK:
            raise HTTPException(400, "nepoznata uloga")
        current = tenancy.role_of(spine, actor.org_id, member_id)
        if current is None:
            raise HTTPException(404, "nije član organizacije")
        # owner ulogu dira samo owner; zadnji owner je nedegradabilan
        if (current == "owner" or body.role == "owner") and actor.role != "owner":
            raise HTTPException(403, "samo owner mijenja owner ulogu")
        if current == "owner" and body.role != "owner":
            owners = spine.read().execute(
                "SELECT COUNT(*) AS n FROM memberships WHERE org_id=? AND role='owner'",
                (actor.org_id,)).fetchone()["n"]
            if owners <= 1:
                raise HTTPException(400, "zadnji owner se ne može degradirati")
        tenancy.add_member(spine, actor.org_id, member_id, body.role, user=actor.username)
        return {"ok": True}

    @app.get("/workers")
    def workers_list(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        out = []
        for m in tenancy.list_members(spine, actor.org_id):
            pol = client_visibility.get_policy(spine, m["user_id"])
            out.append({**m, **pol})
        return out

    @app.post("/workers/{worker_id}/visibility")
    def worker_set_visibility(worker_id: int, body: WorkerVisibilityBody,
                              actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        if tenancy.role_of(spine, actor.org_id, worker_id) is None:
            raise HTTPException(404, "nije član organizacije")
        client_visibility.set_policy(spine, worker_id, body.sees_all,
                                     body.client_ids, actor_name=actor.username)
        return {"ok": True}

    @app.get("/wiki")
    def wiki_list(actor: Actor = Depends(require_actor_web)):
        return wiki_mod.list_pages(spine, actor.org_id)

    @app.get("/wiki/search")
    def wiki_search(q: str, actor: Actor = Depends(require_actor_web)):
        return wiki_mod.search(spine, actor.org_id, q)

    @app.get("/wiki/{slug}")
    def wiki_get(slug: str, actor: Actor = Depends(require_actor_web)):
        page = wiki_mod.get_page(spine, actor.org_id, slug)
        if page is None:
            raise HTTPException(404, "nepoznata stranica")
        return page

    @app.post("/wiki/{slug}/lock")
    def wiki_lock(slug: str, body: WikiLockBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        try:
            wiki_mod.set_locked(spine, actor.org_id, slug, body.locked, user=actor.username)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True}

    def _skill_or_404(skill_id: int, actor: Actor, action: str) -> dict:
        s = skills_mod.get_skill(spine, skill_id)
        if s is None:
            raise HTTPException(404, "nepoznat skill")
        asset = Asset("skill", s["id"], s["org_id"], s["owner_user_id"] or 0,
                      s["visibility"] or "org", s["team_id"])
        if not can(spine, actor, asset, action):  # tvrda org-izolacija + ACL
            raise HTTPException(404 if actor.org_id != s["org_id"] else 403, "nedozvoljeno")
        return s

    @app.get("/skills")
    def skills_list(status: str | None = None, actor: Actor = Depends(require_actor_web)):
        return skills_mod.readable(skills_mod.list_skills(spine, actor.org_id, status), actor)

    @app.post("/skills")
    def skills_create(body: SkillBody, actor: Actor = Depends(require_actor_web)):
        if body.visibility not in VISIBILITIES:
            raise HTTPException(400, "nepoznata vidljivost")
        try:
            sid = skills_mod.create_skill(
                spine, actor.org_id, body.name, body.description, body.trigger,
                body.steps, body.validation, owner_user_id=actor.user_id,
                visibility=body.visibility, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": sid}

    @app.post("/skills/{skill_id}")
    def skills_update(skill_id: int, body: SkillUpdateBody,
                      actor: Actor = Depends(require_actor_web)):
        if body.visibility is not None and body.visibility not in VISIBILITIES:
            raise HTTPException(400, "nepoznata vidljivost")
        _skill_or_404(skill_id, actor, "write")
        return skills_mod.update_skill(spine, skill_id, user=actor.username,
                                       **body.model_dump(exclude_none=True))

    @app.post("/skills/{skill_id}/status")
    def skills_status(skill_id: int, body: SkillStatusBody,
                      actor: Actor = Depends(require_actor_web)):
        _skill_or_404(skill_id, actor, "manage")
        try:
            skills_mod.set_status(spine, skill_id, body.status, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    def _answer(query: str, actor: Actor, fresh: bool = False) -> dict:
        try:
            return pipeline.answer(spine, cfg, query, actor.username,
                                   llm=LLMClient(model_settings.apply(spine, cfg)), fresh=fresh,
                                   actor=actor)
        except (LLMUnavailable, LLMError):
            return {"answer": "LLM trenutno nedostupan ili je vratio grešku.", "lane": "chat",
                    "confidence": 0, "sources": [], "cached": False}

    def _chat_gate(actor: Actor) -> None:
        if not limiter.allow(f"chat:{actor.username}", _CHAT_PER_MIN):
            raise HTTPException(429, "previše upita — pričekajte minutu")

    @app.post("/chat")
    def chat(body: ChatBody, actor: Actor = Depends(require_actor)):
        _chat_gate(actor)
        return _answer(body.q, actor, fresh=body.fresh)

    @app.post("/v1/chat/completions")
    def chat_completions(body: ChatCompletionsBody, actor: Actor = Depends(require_actor)):
        _chat_gate(actor)
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

    # require_user_web: prima i cookie (UI) i Bearer (API klijenti)
    @app.post("/watchlist/run")
    def watchlist_run(user: str = Depends(require_user_web)):
        return [dataclasses.asdict(c) for c in watchlist.check_all(spine, cfg)]

    @app.get("/watchlist/sources")
    def watchlist_list_sources(user: str = Depends(require_user_web)):
        rows = spine.read().execute("SELECT * FROM watch_sources").fetchall()
        return [dict(r) for r in rows]

    @app.post("/watchlist/sources")
    def watchlist_add_source(body: WatchSourceBody, user: str = Depends(require_user_web)):
        sid = watchlist.add_source(spine, body.url, body.category, body.client_id, user, body.kind)
        return {"id": sid}

    @app.post("/watchlist/sources/{source_id}/toggle")
    def watchlist_toggle_source(source_id: int, user: str = Depends(require_user_web)):
        with spine.write() as c:
            r = c.execute("SELECT active FROM watch_sources WHERE id=?", (source_id,)).fetchone()
            if r is None:
                raise HTTPException(404, "nepoznat izvor")
            c.execute("UPDATE watch_sources SET active=? WHERE id=?",
                      (0 if r["active"] else 1, source_id))
        return {"id": source_id, "active": 0 if r["active"] else 1}

    @app.get("/watchlist/upcoming")
    def watchlist_upcoming(user: str = Depends(require_user_web)):
        return [dict(r) for r in spine.read().execute(
            """SELECT u.id, u.effective_date, u.description, s.url
               FROM upcoming_changes u LEFT JOIN watch_sources s ON s.id=u.source_id
               ORDER BY u.effective_date""").fetchall()]

    @app.get("/watchlist/keywords")
    def watchlist_keywords_get(user: str = Depends(require_user_web)):
        return watchlist.get_keywords(spine)

    @app.post("/watchlist/keywords")
    def watchlist_keywords_set(body: KeywordsBody, user: str = Depends(require_user_web)):
        try:
            return watchlist.set_keywords(spine, body.keywords, user=user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/watchlist/export.xlsx")
    def watchlist_export(user: str = Depends(require_user_web)):
        try:
            data = watchlist.export_xlsx(spine)
        except ValueError as e:
            raise HTTPException(503, str(e)) from e
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="pracenje.xlsx"'})

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

    @app.get("/ui/klijenti-uvoz", response_class=HTMLResponse)
    def ui_klijenti_uvoz(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_uvoz import uvoz_page
        return uvoz_page()

    @app.get("/ui/novi-klijent", response_class=HTMLResponse)
    def ui_novi_klijent(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_wizard import wizard_page
        return wizard_page()

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

    @app.get("/ui/pracenje", response_class=HTMLResponse)
    def ui_pracenje(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_pracenje import pracenje_page
        return pracenje_page()

    @app.get("/ui/uredjaji", response_class=HTMLResponse)
    def ui_uredjaji(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_devices import devices_page
        return devices_page()

    @app.get("/ui/posta", response_class=HTMLResponse)
    def ui_posta(request: Request):
        try:
            _require_admin(require_actor_web(request))
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_connectors import connectors_page
        return connectors_page()

    @app.get("/connector-types")
    def connector_types(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.business import connectors as cx
        return cx.list_types()

    @app.get("/connectors")
    def connectors_list(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.business import connectors as cx
        return cx.list_connectors(spine, org_id=actor.org_id)

    @app.post("/connectors/test")
    def connectors_test(body: ConnectorTestBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.business import connectors as cx
        try:
            return cx.test_draft(body.kind, body.config)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/connectors")
    def connectors_create(body: ConnectorCreateBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.business import connectors as cx
        try:
            return cx.create(spine, body.kind, body.name, body.config,
                             cfg=cfg, org_id=actor.org_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/connectors/{cid}/status")
    def connectors_status(cid: int, body: ConnectorStatusBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.business import connectors as cx
        try:
            cx.set_status(spine, cid, body.status, org_id=actor.org_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": cid, "status": body.status}

    @app.delete("/connectors/{cid}")
    def connectors_delete(cid: int, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.business import connectors as cx
        cx.delete(spine, cid, org_id=actor.org_id, user=actor.username)
        return {"id": cid, "removed": True}

    @app.post("/telegram/pairing")
    def telegram_pairing(actor: Actor = Depends(require_actor_web)):
        # self-service: token veže Telegram na TOG korisnika (ne admin za drugoga —
        # inače radnik dobije adminove ovlasti). Svaki prijavljeni korisnik svoj token.
        from ragspine.business.telegram_gateway import create_pairing_token
        token = create_pairing_token(spine, actor.user_id, actor.org_id)
        return {"token": token, "command": f"/start {token}"}

    @app.get("/ui/backup", response_class=HTMLResponse)
    def ui_backup(request: Request):
        try:
            _require_admin(require_actor_web(request))
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_backup import backup_page
        return backup_page()

    @app.get("/backup/list")
    def backup_list(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.ops import backup
        return backup.list_backups(cfg)

    @app.post("/backup")
    def backup_create(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.ops import backup
        b = backup.create_backup(cfg)
        backup.prune(cfg, keep=14)
        spine.audit(actor.username, "backup_create", b["name"])
        return b

    @app.get("/backup/download/{name}")
    def backup_download(name: str, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from ragspine.ops import backup
        try:
            path = backup.resolve_backup(cfg, name)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return FileResponse(path, media_type="application/octet-stream",
                            filename=os.path.basename(path))

    @app.get("/ui/racunalo", response_class=HTMLResponse)
    def ui_racunalo(request: Request):
        # stanje sustava + software inventory = admin (ne svaki radnik)
        try:
            _require_admin(require_actor_web(request))
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_preflight import preflight_page
        return preflight_page()

    @app.get("/preflight")
    def preflight_summary(request: Request):
        from ragspine.web import firstrun
        from ragspine.ops import preflight
        if not firstrun.needs_onboarding(spine):
            _require_admin(require_actor_web(request))  # nakon setupa: admin-only, puni prikaz
            return preflight.summary(cfg)
        # anonimni onboarding: rate-limit + reducirano (bez točnih putanja/OS/GPU
        # detalja koje bi LAN promatrač skupljao) — Codex nalaz
        ip = request.client.host if request.client else "?"
        if not limiter.allow(f"preflight:{ip}", limit=20, window_s=60.0):
            raise HTTPException(429, "previše zahtjeva — pričekajte minutu")
        return preflight.summary(cfg, reduced=True)

    @app.get("/ui/setup", response_class=HTMLResponse)
    def ui_setup(request: Request):
        from ragspine.web import firstrun
        if not firstrun.needs_onboarding(spine):
            return RedirectResponse("/login", status_code=303)  # setup gotov
        from ragspine.web.templates_setup import setup_page
        return setup_page()

    @app.post("/setup/owner")
    def setup_owner(body: SetupOwnerBody, request: Request):
        from ragspine.web import firstrun
        ip = request.client.host if request.client else "?"
        if not limiter.allow(f"setup-owner:{ip}", limit=10, window_s=60.0):
            raise HTTPException(429, "previše pokušaja — pričekajte minutu")
        try:
            firstrun.create_first_owner(spine, body.username.strip(), body.password)
        except ValueError:
            raise HTTPException(409, "operater već postoji — postavljanje je gotovo") from None
        return {"ok": True, "username": body.username.strip()}

    @app.get("/ui/arhitektura", response_class=HTMLResponse)
    def ui_arhitektura(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_arhitektura import arhitektura_page
        return arhitektura_page()

    @app.get("/ui/dok-tipovi", response_class=HTMLResponse)
    def ui_dok_tipovi(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from ragspine.web.templates_doctypes import doctypes_page
        return doctypes_page()

    @app.get("/ui/mape", response_class=HTMLResponse)
    def ui_mape(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return mape_page()

    @app.get("/ui/org", response_class=HTMLResponse)
    def ui_org(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return org_page()

    @app.get("/ui/radnici", response_class=HTMLResponse)
    def ui_radnici(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return radnici_page()

    @app.get("/ui/wiki", response_class=HTMLResponse)
    def ui_wiki(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return wiki_page_ui()

    @app.get("/ui/skills", response_class=HTMLResponse)
    def ui_skills(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return skills_page()

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
    def model_save(body: ModelSettingsBody, actor: Actor = Depends(require_actor_web)):
        # Codex nalaz #7: mijenjanje LLM endpointa je exfiltracijski vektor
        # (bilo koji radnik preusmjeri "mozak" na tuđi server i procuri sve
        # upite) — smije samo admin/vlasnik ureda.
        _require_admin(actor)
        try:
            return model_settings.save(spine, body.provider, body.model, body.base_url,
                                       body.api_key, body.embed_model, body.ollama_url,
                                       actor.username)
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

    # registar mapa mijenja samo admin/owner — role='klijenti' preusmjerava
    # gdje onboarding PIŠE, pa običan radnik ne smije preregistrirati mape
    @app.post("/folders")
    def folders_register(body: FolderBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        try:
            return folders_mod.register(spine, cfg, body.path, body.role, body.label,
                                        actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/folders/{folder_id}")
    def folders_update(folder_id: int, body: FolderUpdateBody,
                       actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        try:
            return folders_mod.update(spine, folder_id, role=body.role, label=body.label,
                                      enabled=body.enabled, user=actor.username)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.delete("/folders/{folder_id}")
    def folders_delete(folder_id: int, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        try:
            folders_mod.remove(spine, folder_id, actor.username)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"id": folder_id, "removed": True}

    @app.post("/folders/{folder_id}/scan")
    def folder_scan_run(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.business import folder_scan as fs
        try:
            res = fs.scan(spine, cfg, folder_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        row = spine.read().execute("SELECT role, label, path FROM folders WHERE id=?",
                                   (folder_id,)).fetchone()
        name = row["label"] or row["path"]
        body = (f"Spojena mapa „{name}\": {res['n_subdirs']} podmapa, {res['n_docs']} dokumenata, "
                f"{res['n_pdf_no_text']} PDF bez pretraživog teksta. Što želiš dalje?")
        with spine.write() as conn:
            exists = conn.execute(
                "SELECT 1 FROM notifications WHERE kind='folder_connected' AND body=? "
                "AND at >= datetime('now','-1 day')", (body,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO notifications(kind, body) VALUES('folder_connected', ?)",
                             (body,))
        return {**res, "notified": True, "role": row["role"]}

    @app.get("/folders/{folder_id}/scan")
    def folder_scan_get(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.business import folder_scan as fs
        return fs.latest(spine, folder_id) or {}

    @app.post("/folders/{folder_id}/ocr")
    def folder_ocr(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.docs import ocr as ocr_mod
        row = spine.read().execute("SELECT path, label FROM folders WHERE id=?",
                                   (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznata mapa")
        base = folders_mod._scoped(cfg, row["path"])
        res = ocr_mod.bulk_ocr(spine, cfg, base)
        from ragspine.business import folder_scan as fs
        fs.scan(spine, cfg, folder_id)  # osvježi brojke — dugme ne smije ostati stale
        name = row["label"] or row["path"]
        body = f"OCR gotov za „{name}\": {res['processed']} obrađeno, {res['skipped']} preskočeno."
        if res.get("signed"):
            body += f" {res['signed']} potpisanih netaknuto."
        if res.get("ocr_empty"):
            body += f" {res['ocr_empty']} nečitljivih."
        with spine.write() as conn:
            conn.execute("INSERT INTO notifications(kind, body) VALUES('folder_ocred', ?)", (body,))
        return {**res, "notified": True}

    @app.get("/folders/{folder_id}/ocr/audit")
    def folder_ocr_audit(folder_id: int, user: str = Depends(require_user_web)):
        from ragspine.docs import ocr as ocr_mod
        row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznata mapa")
        base = folders_mod._scoped(cfg, row["path"])
        return ocr_mod.audit_folder(cfg, base)

    @app.post("/notes/folder")
    def folder_note(body: FolderNoteBody, user: str = Depends(require_user_web)):
        key = f"note:folder:{body.folder_id}" if body.folder_id is not None else "note:global"
        with spine.write() as c:
            c.execute("INSERT INTO memory(user,key,value) VALUES(?,?,?) "
                      "ON CONFLICT(user,key) DO UPDATE SET value=excluded.value",
                      (user, key, body.body))
        return {"ok": True, "key": key}

    @app.get("/ui/dokumenti", response_class=HTMLResponse)
    def ui_dokumenti(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return dokumenti_page()

    @app.get("/notifications.json")
    def notifications_json(actor: Actor = Depends(require_actor_web)):
        rows = spine.read().execute(
            "SELECT id, kind, body, client_id, seen, at FROM notifications ORDER BY at DESC LIMIT 50"
        ).fetchall()
        return _visible_rows(actor, [dict(r) for r in rows])

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

    @app.get("/doc-types")
    def doc_types_list(user: str = Depends(require_user_web)):
        return doc_registry.list_types(spine)

    @app.post("/doc-types")
    def doc_types_upsert(body: DocTypeBody, user: str = Depends(require_user_web)):
        try:
            key = doc_registry.upsert(spine, body.key, body.label,
                                      [f.model_dump() for f in body.fields],
                                      active=bool(body.active), sort=body.sort, user=user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"key": key}

    @app.post("/extract")
    def extract_run(body: ExtractBody, actor: Actor = Depends(require_actor_web)):
        from ragspine.docs import extraction as extraction_mod
        # radnik ne smije ekstraktati tuđi dokument ni pripisati ga tuđem klijentu
        drow = spine.read().execute("SELECT client_id FROM documents WHERE id=?",
                                    (body.doc_id,)).fetchone()
        if drow is not None and drow["client_id"] is not None:
            _guard_client(actor, drow["client_id"])
        if body.client_id is not None:
            _guard_client(actor, body.client_id)
        # LLM je fallback — bez konfiguriranog providera ekstrakcija ide regex-only
        try:
            llm = LLMClient(model_settings.apply(spine, cfg))
        except Exception:
            llm = None
        try:
            return extraction_mod.extract(spine, cfg, body.doc_id, body.doc_type,
                                          llm=llm, client_id=body.client_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # Uređaji (E): registar mijenja admin; sken/print koristi svaki radnik
    # (uz odabir uređaja pri akciji).
    @app.get("/devices")
    def devices_list(kind: str | None = None, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import devices as devices_mod
        return devices_mod.list_devices(spine, kind=kind)

    @app.get("/devices/discover")
    def devices_discover(actor: Actor = Depends(require_actor_web)):
        from ragspine.core import lan
        _require_admin(actor)
        return lan.discover()

    @app.post("/devices")
    def devices_add(body: DeviceBody, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import devices as devices_mod
        from ragspine.core import lan
        _require_admin(actor)
        try:
            return devices_mod.add_device(spine, body.kind, body.name, body.url,
                                          user=actor.username)
        except (ValueError, lan.LanBlocked) as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/devices/{device_id}")
    def devices_delete(device_id: int, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import devices as devices_mod
        _require_admin(actor)
        try:
            devices_mod.remove_device(spine, device_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"id": device_id, "removed": True}

    @app.post("/devices/{device_id}/scan")
    def devices_scan(device_id: int, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import devices as devices_mod
        from ragspine.core import lan
        try:
            return devices_mod.scan(spine, cfg, device_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except (lan.LanBlocked, RuntimeError) as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/devices/{device_id}/print")
    def devices_print(device_id: int, body: PrintBody, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import devices as devices_mod
        from ragspine.core import lan
        row = spine.read().execute("SELECT client_id FROM documents WHERE id=?",
                                   (body.doc_id,)).fetchone()
        if row is None:
            raise HTTPException(400, "nepoznat dokument")
        if row["client_id"] is not None:
            _guard_client(actor, row["client_id"])  # radnik ne printa tuđe klijente
        try:
            return devices_mod.print_doc(spine, cfg, device_id, body.doc_id,
                                         user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except (lan.LanBlocked, RuntimeError) as e:
            raise HTTPException(502, str(e)) from e

    # Arhitektura mapa (D2): dogovor o strukturi ureda = admin/owner posao.
    # Preview lista SVE klijente s diska (KLIJENTI mapa) pa nije za
    # restringirane radnike.
    @app.get("/folder-architecture")
    def folder_architecture_preview(actor: Actor = Depends(require_actor_web)):
        from ragspine.business import folder_architecture as fa
        _require_admin(actor)
        try:
            return fa.propose(spine, cfg)
        except ValueError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/folder-architecture/learned")
    def folder_architecture_learned(actor: Actor = Depends(require_actor_web)):
        from ragspine.business import folder_architecture as fa
        _require_admin(actor)
        try:
            return fa.learn_structure(spine, cfg)
        except ValueError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/folder-architecture/template")
    def folder_architecture_template_get(actor: Actor = Depends(require_actor_web)):
        from ragspine.business import folder_architecture as fa
        _require_admin(actor)
        return fa.get_template(spine)

    @app.post("/folder-architecture/template")
    def folder_architecture_template_set(body: ArchTemplateBody,
                                         actor: Actor = Depends(require_actor_web)):
        from ragspine.business import folder_architecture as fa
        _require_admin(actor)
        try:
            return fa.set_template(spine, office=body.office,
                                   client_subdirs=body.client_subdirs,
                                   user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/folder-architecture/apply")
    def folder_architecture_apply(actor: Actor = Depends(require_actor_web)):
        from ragspine.business import folder_architecture as fa
        _require_admin(actor)  # kreira mape za SVE klijente — samo admin/owner
        try:
            return fa.apply(spine, cfg, user=actor.username)
        except ValueError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/doc-types/export")
    def doc_types_export(user: str = Depends(require_user_web)):
        return JSONResponse(
            doc_registry.export_json(spine),
            headers={"Content-Disposition": 'attachment; filename="doc_types.json"'})

    @app.get("/clients/{client_id}/obveze-postavke")
    def client_obligations_get(client_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
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
                                actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
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
            obveze.set_client_types(spine, client_id, body.manual_kinds, user=actor.username)
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
    def expiry_expiring(days: int = 60, actor: Actor = Depends(require_actor_web)):
        return _visible_rows(actor, [dict(r) for r in expiry_mod.expiring(spine, days)])

    @app.post("/expiry")
    def expiry_add(body: ExpiryBody, actor: Actor = Depends(require_actor_web)):
        if body.client_id is not None:
            _guard_client(actor, body.client_id)
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
                      actor: Actor = Depends(require_actor_web)):
        if client_id is not None:
            _guard_client(actor, client_id)
        rows = [dict(r) for r in notes.search(spine, term=q, client_id=client_id)]
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        if vis is not None:  # restringirani radnik ne vidi bilješke tuđih klijenata
            rows = [r for r in rows if r.get("client_id") in vis]
        return rows

    @app.post("/notes")
    def notes_add(body: NoteBody, actor: Actor = Depends(require_actor_web)):
        if body.client_id is not None:
            _guard_client(actor, body.client_id)
        note_id = notes.add(spine, body.client_id, actor.username, body.body)
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
    def messaging_send(body: MessagingSendBody, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, body.client_id)  # radnik ne šalje poruke tuđim klijentima
        return messaging.send_to_client(spine, cfg, body.client_id, body.subject, body.body,
                                         dry_run=body.dry_run)

    @app.post("/messaging/campaign")
    def messaging_campaign(body: MessagingCampaignBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)  # masovni izlaz prema svim klijentima = admin posao
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
    def messaging_log(client_id: int | None = None, actor: Actor = Depends(require_actor_web)):
        if client_id is not None:
            _guard_client(actor, client_id)
            rows = spine.read().execute(
                "SELECT * FROM message_log WHERE client_id=? ORDER BY at DESC LIMIT 50", (client_id,)
            ).fetchall()
        else:
            rows = spine.read().execute(
                "SELECT * FROM message_log ORDER BY at DESC LIMIT 50"
            ).fetchall()
        out = [dict(r) for r in rows]
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        if vis is not None:  # restringirani radnik ne vidi log tuđih klijenata
            out = [r for r in out if r.get("client_id") in vis]
        return out

    def _guard_client(actor: Actor, client_id: int) -> None:
        if not client_visibility.can_see(spine, actor.user_id, client_id, actor.role):
            raise HTTPException(403, "nemate pristup ovom klijentu")

    def _visible_rows(actor: Actor, rows: list) -> list:
        """Odbaci retke skrivenih klijenata (client_id IS NULL = uredski, ostaje
        svima). Zajednički filtar za agregatne read-liste (expiry, notifications)."""
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        if vis is None:
            return rows
        return [r for r in rows if r.get("client_id") is None or r.get("client_id") in vis]

    @app.post("/clients/assist")
    def client_assist(body: ClientAssistBody, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import client_assist
        # server-side throttle — klijentski debounce se da zaobići, LLM košta
        if not limiter.allow(f"assist:{actor.user_id}", limit=20, window_s=60.0):
            raise HTTPException(429, "previše assist zahtjeva — uspori tipkanje")
        try:
            llm = LLMClient(model_settings.apply(spine, cfg))
        except Exception:
            llm = None
        return client_assist.assist(spine, cfg, body.model_dump(), llm=llm, actor=actor)

    @app.get("/clients/{client_id}/doc-types")
    def client_doc_types_get(client_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
        return [r["doc_type_key"] for r in spine.read().execute(
            "SELECT doc_type_key FROM client_doc_types WHERE client_id=? ORDER BY doc_type_key",
            (client_id,)).fetchall()]

    @app.post("/clients")
    def client_create(body: ClientCreateBody, actor: Actor = Depends(require_actor_web)):
        try:
            result = onboarding.create_client(spine, cfg, body.model_dump(), actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        # restringirani kreator inače ne bi vidio vlastito djelo
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        if vis is not None:
            client_visibility.grant(spine, actor.user_id, result["id"], actor.username)
        return {"id": result["id"], "nas_folder": result["nas_folder"]}

    @app.get("/clients/discover")
    def clients_discover(folder_id: int, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import client_discovery
        _require_admin(actor)  # enumeracija/kreiranje klijenata iz NAS mape = admin
        try:
            return client_discovery.discover(spine, cfg, folder_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/clients/discover/commit")
    def clients_discover_commit(body: DiscoverCommitBody, actor: Actor = Depends(require_actor_web)):
        from ragspine.business import client_discovery
        _require_admin(actor)
        try:
            return client_discovery.commit(spine, cfg, body.folder_id, body.items)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/clients")
    def clients_list(actor: Actor = Depends(require_actor_web)):
        rows = spine.read().execute(
            "SELECT id, name, oib, pdv_status, industry, regime, active FROM clients ORDER BY name"
        ).fetchall()
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        return [dict(r) for r in rows if vis is None or r["id"] in vis]

    @app.get("/clients/{client_id}")
    def client_get(client_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
        row = spine.read().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznat klijent")
        return dict(row)

    @app.post("/clients/{client_id}/document")
    def client_document_add(client_id: int, body: ClientDocumentBody,
                             actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
        if spine.read().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone() is None:
            raise HTTPException(404, "nepoznat klijent")
        try:
            data = base64.b64decode(body.data_base64, validate=True)
        except binascii.Error as e:
            raise HTTPException(400, "neispravan base64") from e
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "dokument prevelik (max 25MB)")
        try:
            return onboarding.add_document(spine, cfg, client_id, body.filename, data,
                                           owner=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/clients/{client_id}/documents")
    def client_documents_list(client_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
        try:
            return onboarding.list_documents(spine, cfg, client_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/clients/{client_id}/karton.json")
    def client_karton(client_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
        try:
            return karton_mod.karton_data(spine, cfg, client_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/clients/{client_id}/messaging")
    def client_messaging_set(client_id: int, body: ClientMessagingBody,
                              actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
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
    def cjenik_izracun(body: CjenikIzracunBody, actor: Actor = Depends(require_actor_web)):
        if body.client_id is not None:
            _guard_client(actor, body.client_id)
        try:
            return cjenik.izracunaj_cijenu(spine, body.client_id, employees=body.employees,
                                            extras=body.extras)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/cjenik/usporedba/{client_id}")
    def cjenik_usporedba(client_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
        try:
            return cjenik.usporedi_s_trzistem(spine, client_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/clients/{client_id}/pausal")
    def client_pausal_set(client_id: int, body: PausalBody, actor: Actor = Depends(require_actor_web)):
        _guard_client(actor, client_id)
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
    def doc_generate(body: DocGenerateBody, actor: Actor = Depends(require_actor_web)):
        if body.client_id is not None:
            _guard_client(actor, body.client_id)
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
                      action: str | None = None,
                      actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)  # audit trag otkriva tuđe akcije — nije za svakog člana
        # Codex nalaz: bez org-filtra admin org-a A vidi audit org-a B
        rows = auditlog.search(spine, client=client, user=user, action=action,
                               org_id=actor.org_id)
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

    def _maybe_start_telegram():
        """Ako je telegram_gateway konektor uključen, pokreni poll-thread. Bez
        konfiguriranog konektora (npr. u testovima) ne pokreće ništa."""
        import threading
        from ragspine.business import connectors as cx
        from ragspine.business import telegram_gateway as tgw
        row = spine.read().execute(
            "SELECT id FROM connectors WHERE kind='telegram_gateway' "
            "AND status IN ('connected','pending') ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return
        got = cx.config_for_adapter(spine, row["id"], cfg)
        token = (got[1].get("bot_token") if got else "") or ""
        if not token:
            return
        stop = threading.Event()
        app.state.tg_stop = stop
        app.state.tg_thread = threading.Thread(
            target=tgw.poll_loop, args=(spine, cfg, token, _answer, stop),
            kwargs={"limiter": limiter, "key": f"c{row['id']}"},  # jedinstven offset po konektoru
            daemon=True, name="telegram-gateway")
        app.state.tg_thread.start()

    @app.on_event("shutdown")
    def _stop_telegram():
        ev = getattr(app.state, "tg_stop", None)
        th = getattr(app.state, "tg_thread", None)
        if ev is not None:
            ev.set()
        if th is not None:
            th.join(timeout=5)  # graceful stop+join

    _maybe_start_telegram()
    return app
