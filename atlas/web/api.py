import base64
import binascii
import dataclasses
import json
import os
import re
import secrets
from datetime import date
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from atlas.business import auditlog
from atlas.business import checklist
from atlas.business import cjenik
from atlas.business import client_visibility
from atlas.business import dashboard
from atlas.business import expiry as expiry_mod
from atlas.business import feedback_learn
from atlas.business import folders as folders_mod
from atlas.business import kalendar
from atlas.business import karton as karton_mod
from atlas.business import knjizenje  # noqa: F401 — register knjizenje lane handler
from atlas.business import model_settings
from atlas.business import monthly
from atlas.business import nldate
from atlas.business import notes
from atlas.business import doc_registry, obveze
from atlas.business import onboarding
from atlas.business import fleet
from atlas.business import peer_compare
from atlas.business import power as power_mod
from atlas.business import sop as sop_mod
from atlas.business import sop_images
from atlas.business import tenancy
from atlas.business.acl import ROLE_RANK, VISIBILITIES, Actor, Asset, can
from atlas.knowledge import skills as skills_mod
from atlas.knowledge import wiki as wiki_mod
from atlas.business import messaging
from atlas.web import static as static_mod
from atlas.browser import agent as agent_mod
from atlas.browser.bridge import Bridge
from atlas.core import memory as memory_mod
from atlas.core import optional
from atlas.core.llm import LLMClient, LLMError, LLMUnavailable
from atlas.core.security import hash_password, jwt_encode, verify_password
from atlas.docs import doc_generator, ocr, vault
from atlas.knowledge import features as features_mod
from atlas.knowledge import patterns as patterns_mod
from atlas.knowledge import translate as translate_mod
from atlas.ops import doctor, health, model_recommender, nis2
from atlas.rag import agent, agent_tools
from atlas.rag import pipeline
from atlas.rag import router as router_mod
from atlas.rag import versioning
from atlas.rag import sql_lane, graphrag  # noqa: F401 — register sql/graph lane handlers
from atlas.web import learn  # noqa: F401 — register learn lane handler
from atlas.web.ratelimit import RateLimiter
from atlas.web import watchlist
from atlas.web import websearch  # noqa: F401 — register web lane handler
from atlas.web.deps import (COOKIE_NAME, add_user, require_actor, require_actor_web, require_user,
                               require_user_web)
from atlas.web.templates_login import render_login
from atlas.web.templates_mape import mape_page
from atlas.web.templates_model import model_page
from atlas.web.templates_obveze import obveze_none_page, obveze_types_page, render_obveze
from atlas.web.templates_org import (org_page, radnici_page, skills_page,
                                        vidljivost_page, wiki_page as wiki_page_ui)
from atlas.web.templates_ui import (chat_page, dashboard_page, dokumenti_page, klijent_page,
                                        klijenti_page, obavijesti_page, postavke_page, upute_page)


class ChatBody(BaseModel):
    q: str
    fresh: bool = False


class PendingTokenBody(BaseModel):
    token: str
    remember: bool = False  # "confirm and remember" -> create a user-grant (except high-risk)


class GrantBody(BaseModel):
    tool: str
    args: dict = {}
    scope: str = "user"          # 'user' (own) or 'org' (whole office; owner)
    days: int | None = None      # None = permanent


class UredPravilaBody(BaseModel):
    pravila: str = ""


class BudgetBody(BaseModel):
    caps: dict[str, int] = {}    # {llm|tokens|writes: cap}; 0 = no limit


class ScheduledTaskBody(BaseModel):
    title: str = ""
    action_key: str
    params: dict = {}
    day_of_month: int | None = None
    hour: int = 8


class PowerConfigBody(BaseModel):
    # 'armed' DELIBERATELY omitted — arming goes only through /napajanje/arm
    # (one place, audited). Otherwise a config-POST would silently arm shutdown.
    enabled: bool | None = None
    nut_host: str | None = None
    nut_port: int | None = None
    ups_name: str | None = None
    on_battery_seconds: int | None = None


class ArmBody(BaseModel):
    armed: bool


class AgentResultBody(BaseModel):
    id: int
    ok: bool = True
    detail: str = ""


class NaredbaBody(BaseModel):
    action: str
    program_key: str | None = None


class ProgramBody(BaseModel):
    key: str
    label: str = ""


class EnrollBody(BaseModel):
    name: str | None = None


class ApproveBody(BaseModel):
    device_name: str | None = None


class ObligationFieldBody(BaseModel):
    label: str
    type: str = "text"


class FieldValueBody(BaseModel):
    key: str
    value: str | float | int | bool | None = None


_AGENT_ROLES = ("member", "admin", "owner")  # viewer stays on the retrieval path
_PENDING_TTL_MIN = 10

# ── PWA (wraps the web UI as an installable application) ──────────────────
_PWA_THEME = "#4f46e5"
_PWA_MANIFEST = json.dumps({
    "name": "ATLAS", "short_name": "ATLAS",
    "description": "Ured: dokumenti, obveze, klijenti — lokalni AI asistent.",
    "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": _PWA_THEME, "theme_color": _PWA_THEME, "lang": "hr", "dir": "ltr",
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}, ensure_ascii=False)

# Minimal SW: cache-first for /static/, network-first for navigations with an
# offline fallback. NEVER caches authed HTML (freshness/privacy).
_PWA_SW_JS = """
const CACHE = 'atlas-v1';
const SHELL = ['/static/icons/icon-192.png', '/static/icons/favicon.svg',
               '/manifest.webmanifest', '/offline.html'];
self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); })
    .then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') { return; }
  var url = new URL(req.url);
  if (url.pathname.indexOf('/static/') === 0) {
    e.respondWith(caches.match(req).then(function (r) {
      return r || fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); });
        return res;
      });
    }));
    return;
  }
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(function () { return caches.match('/offline.html'); }));
  }
});
"""

_PWA_OFFLINE_HTML = """<!DOCTYPE html>
<html lang="hr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATLAS — nema veze</title>
<style>body{font-family:system-ui,sans-serif;background:#4f46e5;color:#fff;
display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center;text-align:center}
div{max-width:22rem;padding:2rem}</style></head>
<body><div><h1>ATLAS</h1><p>Nema veze sa serverom. Provjeri je li glavno računalo
upaljeno i na mreži, pa osvježi stranicu.</p></div></body></html>"""


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
    # min 8 = basic strength; max 128 = protects PBKDF2 from huge input (Codex)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginStepBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class LoginActivateBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    password2: str = Field(min_length=8, max_length=128)


class RadnikCreateBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    role: str = "member"


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


class DeviceCaps(BaseModel):
    shutdown_order: int | None = None
    wol: bool = False
    run_programs: bool = False
    monitor_only: bool = False


class DeviceBody(BaseModel):
    kind: str
    name: str
    url: str = ""
    host: str | None = None
    mac: str | None = None
    worker_username: str | None = None
    caps: DeviceCaps | None = None


class DeviceUpdateBody(BaseModel):
    name: str | None = None
    host: str | None = None
    mac: str | None = None
    worker_username: str | None = None
    caps: DeviceCaps | None = None


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


class FallbackProfile(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    ollama_url: str = ""


class FallbacksBody(BaseModel):
    profiles: list[FallbackProfile] = []


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
    # /docs + /redoc + /openapi.json disabled: Swagger UI pulls JS/CSS from a CDN
    # (fails under a strict CSP), and the interactive API explorer is a needless
    # unauthenticated surface enumeration on a production LAN.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.spine = spine
    app.state.cfg = cfg
    app.state.bridge = Bridge()
    # The DB model choice (wizard/settings) must apply outside the LLM request path
    # too — embed._get_model() reads the global get_config() directly (no access to
    # spine), so the override is applied once here. Idempotent: model_settings.apply
    # on an already-applied cfg is a no-op if the DB choice hasn't changed since.
    from atlas.config import set_config
    set_config(model_settings.apply(spine, cfg))
    tenancy.backfill_org(spine)  # idempotent: existing documents/knowledge -> default org
    limiter = RateLimiter()
    _LOGIN_PER_MIN, _LOGIN_IP_PER_MIN, _CHAT_PER_MIN = 10, 30, 30  # ponytail: config knob only when needed
    from atlas.business.connector_adapters import register_builtin
    register_builtin()  # registers mail/telegram/whatsapp types (idempotent)

    # Security headers (defense-in-depth): clickjacking, MIME-sniff, referrer
    # leak, base-tag hijack. CSP allows 'unsafe-inline' because the UI is inline-script,
    # but no 'unsafe-eval' (no eval/Function) and no external sources (LAN/offline).
    _CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'")

    _MAX_BODY = 64 * 1024 * 1024  # 64MB: above 25MB base64 upload (~34MB), below DoS territory

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        # pre-decode gate: Starlette buffers the whole body before the handler, so
        # 1GB JSON = 1.75GB RAM before a 400 — reject by Content-Length immediately.
        # ponytail: catches only requests with a Content-Length; chunked/streaming
        # without it passes — the real hard limit is on the reverse proxy (deploy
        # doc), this is a cheap first filter for a typical JSON bomb.
        clen = request.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > _MAX_BODY:
            return JSONResponse({"detail": "tijelo zahtjeva preveliko", "max_bytes": _MAX_BODY},
                                status_code=413)
        # first-run gatekeeper: while no user exists yet, navigation goes to the
        # wizard (/ui/setup). Modeled on Open WebUI (has_users()==False).
        from atlas.web import firstrun
        if request.method == "GET" and firstrun._redirect_target(request.url.path) \
                and firstrun.needs_setup(spine):
            return RedirectResponse("/ui/setup", status_code=303)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = _CSP
        resp.headers["Server"] = "ATLAS"
        if cfg.https_only:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    app.include_router(static_mod.router)

    @app.get("/health")
    def health():
        # Public: installer/monitoring check that this IS an ATLAS server (option B:
        # the workstation binds only once the server answers atlas=true + setup done).
        import atlas
        from atlas.ops import wizard_state
        return {"status": "ok", "atlas": True, "version": atlas.__version__,
                "setup_complete": wizard_state.is_complete(spine),
                "missing": optional.missing()}

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        return render_login()

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.post("/login/step")
    def login_step(body: LoginStepBody, request: Request):
        """First step of the two-step login: without revealing whether users exist —
        an unknown user returns the same 'password' state as an already-activated one."""
        ip = request.client.host if request.client else "?"
        if not limiter.allow(f"login-ip:{ip}", _LOGIN_IP_PER_MIN):
            raise HTTPException(429, "previše pokušaja — pričekajte minutu")
        if not limiter.allow(f"login:{ip}:{body.username}", _LOGIN_PER_MIN):
            raise HTTPException(429, "previše pokušaja — pričekajte minutu")
        row = spine.read().execute(
            "SELECT pw_hash FROM users WHERE username=?", (body.username,)).fetchone()
        if row is not None and not row["pw_hash"]:
            return {"state": "activate"}
        return {"state": "password"}

    def _is_admin_tier_pending(user_id: int, sys_role: str) -> bool:
        """C1: prevents self-service activation of an admin/owner account — an attacker
        who guesses a pending username must not be able to turn POST /login/activate
        into a full office takeover without any token. Checks both the sys-role column
        (how the /radnici POST records the role) and the actual org membership role (owner/admin)."""
        if sys_role == "admin":
            return True
        m = spine.read().execute(
            "SELECT role FROM memberships WHERE user_id=? ORDER BY id LIMIT 1",
            (user_id,)).fetchone()
        return m is not None and m["role"] in ("admin", "owner")

    @app.post("/login/activate")
    def login_activate(body: LoginActivateBody, request: Request):
        ip = request.client.host if request.client else "?"
        if not limiter.allow(f"login-ip:{ip}", _LOGIN_IP_PER_MIN):
            raise HTTPException(429, "previše pokušaja — pričekajte minutu")
        if not limiter.allow(f"login:{ip}:{body.username}", _LOGIN_PER_MIN):
            raise HTTPException(429, "previše pokušaja — pričekajte minutu")
        if body.password != body.password2:
            raise HTTPException(400, "lozinke se ne poklapaju")
        row = spine.read().execute(
            "SELECT id, role, pw_hash FROM users WHERE username=?", (body.username,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznat korisnik")
        if row["pw_hash"]:
            raise HTTPException(409, "račun je već aktiviran")
        if _is_admin_tier_pending(row["id"], row["role"]):
            raise HTTPException(403, "administratorski račun se ne aktivira ovim putem — "
                                     "neka te postojeći administrator uvede (admin-kao-radnik ulaz)")
        new_hash = hash_password(body.password)
        with spine.write() as c:
            # WHERE pw_hash IS NULL = atomic race guard: if someone else already
            # activated between the SELECT and this UPDATE, rowcount is 0.
            cur = c.execute("UPDATE users SET pw_hash=? WHERE id=? AND pw_hash IS NULL",
                             (new_hash, row["id"]))
            if cur.rowcount == 0:
                raise HTTPException(409, "račun je već aktiviran")
        spine.audit(body.username, "user_activate", f"user:{row['id']}", f"user aktiviran ip={ip}")
        org_id, _ = tenancy.resolve_login_org(spine, row["id"], row["role"])
        token = jwt_encode({"sub": body.username, "role": row["role"],
                            "uid": row["id"], "org_id": org_id}, cfg.jwt_secret)
        resp = JSONResponse({"token": token})
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=86400,
                         secure=cfg.https_only)
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
        # IP-only limiter alongside per-user: without it an attacker churning junk
        # usernames fills the limiter past its cap and evicts the victim's blocked
        # bucket (Codex r3). 30/min per IP also caps the rate of new key creation.
        if not limiter.allow(f"login-ip:{ip}", _LOGIN_IP_PER_MIN):
            raise HTTPException(429, "previše pokušaja prijave s ove adrese — pričekajte minutu")
        if not limiter.allow(f"login:{ip}:{username}", _LOGIN_PER_MIN):
            raise HTTPException(429, "previše pokušaja prijave — pričekajte minutu")
        row = spine.read().execute(
            "SELECT id, pw_hash, role FROM users WHERE username=?", (username,)
        ).fetchone()
        # I1 (timing enumeration): the admin-fallback loop below does N extra
        # verify_password calls for a KNOWN user with a wrong password (N = number
        # of admin/owner members in their org) — without equivalent work for an
        # UNKNOWN username that difference in the count of PBKDF2 calls reveals the
        # existence of an account through latency, defeating the anti-enumeration
        # of /login/step. Fix: an unknown username does the SAME shape of work — a
        # dummy "own" check + a dummy fallback loop of the same length as the
        # default-org admin/owner count (the only org the app reaches through the
        # public API — see tenancy.default_org_id/resolve_login_org).
        def _admin_tier_count(org_id: int) -> int:
            return spine.read().execute(
                "SELECT COUNT(*) AS n FROM memberships WHERE org_id=? AND role IN ('admin','owner')",
                (org_id,)).fetchone()["n"]

        if row is None:
            verify_password(password, _DUMMY_PW_HASH)  # constant-time-ish: keep latency ~equal
            for _ in range(_admin_tier_count(tenancy.default_org_id(spine))):
                verify_password(password, _DUMMY_PW_HASH)
            raise HTTPException(401, "invalid credentials")
        # Admin-as-worker: own password first; if it fails (or pw_hash is NULL
        # because the worker awaits activation), try the OWNER/ADMIN hashes of the
        # SAME org — success = login AS the worker (the role comes fresh from
        # membership below, it does not escalate this way), only audit +
        # impersonated_by claim record who logged in.
        # I1(a) (re-review): pw_hash NULL (pending worker/admin) short-circuits
        # through verify_password WITHOUT any PBKDF2 call (security.py guard) —
        # every check against a "possibly non-existent hash" must always spend
        # exactly one real PBKDF2, otherwise a pending account leaks as measurably
        # faster. You must NOT just use "stored or _DUMMY_PW_HASH": _DUMMY_PW_HASH
        # is a known constant in the source — without this wrapper an attacker who
        # sends EXACTLY that dummy password would get matched=True on any pending
        # account (and even a crash on row['pw_hash'].startswith(None)).
        def _verify_always_pbkdf2(pw: str, stored: str | None) -> bool:
            if stored:
                return verify_password(pw, stored)
            verify_password(pw, _DUMMY_PW_HASH)  # cost, result is discarded
            return False

        impersonated_by = None
        imp_role = None
        imp_uid = None
        own_ok = _verify_always_pbkdf2(password, row["pw_hash"])
        if not own_ok:
            m = spine.read().execute(
                "SELECT org_id, role FROM memberships WHERE user_id=? ORDER BY id LIMIT 1",
                (row["id"],)).fetchone()
            if m is not None:
                # I1(b) (re-review): does NOT exclude itself (an active admin/owner
                # target must still appear in this loop — otherwise an admin-tier
                # target has one PBKDF2 call fewer than a member/unknown user, again
                # measurably "faster"). A redundant re-check of one's own hash is
                # harmless (deterministically already missed in own_ok above).
                admins = spine.read().execute(
                    """SELECT u.id AS imp_uid, u.username, u.pw_hash, mm.role AS imp_role
                       FROM memberships mm JOIN users u ON u.id = mm.user_id
                       WHERE mm.org_id=? AND mm.role IN ('admin','owner')""",
                    (m["org_id"],)).fetchall()
                for a in admins:
                    # Does NOT break after a match — every candidate spends exactly one
                    # PBKDF2 (otherwise a rank-denied 401 (1+k) is measurably different
                    # from invalid (1+N) and reveals that a privileged password was hit; Codex).
                    matched = _verify_always_pbkdf2(password, a["pw_hash"])
                    if matched and a["username"] != username and impersonated_by is None:
                        impersonated_by = a["username"]
                        imp_role = a["imp_role"]
                        imp_uid = a["imp_uid"]
            else:
                # without membership (legacy CLI account): no target org for a real
                # check, but still do a dummy loop of the same length as the default
                # org — otherwise THIS known-user branch leaks timing again.
                for _ in range(_admin_tier_count(tenancy.default_org_id(spine))):
                    verify_password(password, _DUMMY_PW_HASH)
            # escalation gate (post-match, does not affect timing): the impersonator
            # must STRICTLY outrank the target's role — an admin may activate a
            # member/viewer, NEVER an owner or another admin (otherwise an admin's
            # password -> owner session). Target role comes from MEMBERSHIP (m['role'])
            # — users.role ('radnik') is a different vocabulary so it must not be
            # compared with the impersonator's membership role.
            target_role = m["role"] if m is not None else "owner"
            if impersonated_by is not None and (
                    ROLE_RANK.get(imp_role, -1) <= ROLE_RANK.get(target_role, 99)):
                spine.audit(impersonated_by, "impersonate_denied", f"user:{username}",
                            f"{imp_role} ne smije preuzeti {target_role}")
                impersonated_by = None
                imp_uid = None
            if impersonated_by is None:
                raise HTTPException(401, "invalid credentials")
        if own_ok and not row["pw_hash"].startswith("pbkdf2$"):
            # legacy 200k hash (no prefix): rehash to current parameters on a
            # successful login — we only have the password now, in plaintext.
            # Applies ONLY to the own password — the admin fallback does not touch the worker's hash.
            with spine.write() as c:
                c.execute("UPDATE users SET pw_hash=? WHERE id=?",
                          (hash_password(password), row["id"]))
        # uid+org_id are pointers for the Actor lookup; the org role is NOT put in
        # the token (it is read fresh from memberships on every request).
        org_id, _ = tenancy.resolve_login_org(spine, row["id"], row["role"])
        payload = {"sub": username, "role": row["role"], "uid": row["id"], "org_id": org_id}
        if impersonated_by:
            payload["impersonated_by"] = impersonated_by
            payload["imp_uid"] = imp_uid  # permanent ceiling: checked on EVERY request
            spine.audit(impersonated_by, "impersonate", f"user:{username}",
                        f"admin {impersonated_by} ušao kao {username}")
        token = jwt_encode(payload, cfg.jwt_secret)
        # ponytail: CSRF for POST /obveze/mark deferred — SameSite=Lax already blocks
        # cross-site POST-with-cookie on top-level navigation; a CSRF token is the
        # upgrade path if that stops being sufficient (e.g. subdomain untrusted).
        resp = JSONResponse({"token": token}) if is_json else RedirectResponse("/obveze", status_code=303)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=86400,
                         secure=cfg.https_only)
        return resp

    @app.get("/v1/models")
    def models(user: str = Depends(require_user)):
        return {"object": "list", "data": [{"id": cfg.llm_model or "atlas", "object": "model"}]}

    def _require_admin(actor: Actor) -> Actor:
        if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["admin"]:
            raise HTTPException(403, "potrebna admin uloga")
        return actor

    def _require_owner(actor: Actor) -> Actor:
        # Power/shutdown is MACHINE-level (one physical UPS/server) and global to
        # the installation — not per organization. So mutations require the HIGHEST
        # role (owner), not just any tenant-admin.
        if actor.role != "owner":
            raise HTTPException(403, "potrebna owner uloga (strojno napajanje)")
        return actor

    def _active_owner_count(org_id: int) -> int:
        """I2 (re-review): counts ONLY owner members with a password set.
        A pending owner (pw_hash NULL) can neither log in nor be started by an
        admin-as-worker without any OTHER admin/owner — counting them as "cover"
        would be a bypass: promote a pending member to owner (count=2 includes the
        pending one), delete yourself (passes the last-owner check), leaving only a
        pending owner whom C1 blocks from self-activation -> permanent lockout."""
        return spine.read().execute(
            """SELECT COUNT(*) AS n FROM memberships mm JOIN users u ON u.id = mm.user_id
               WHERE mm.org_id=? AND mm.role='owner' AND u.pw_hash IS NOT NULL""",
            (org_id,)).fetchone()["n"]

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
        # add is insert-only: an existing member's role is changed EXCLUSIVELY
        # through /org/members/{id}/role (which carries owner-only + last-owner
        # guards) — otherwise an admin could demote an owner via an "add" upsert.
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
        # only an owner touches the owner role; the last owner is non-demotable
        if (current == "owner" or body.role == "owner") and actor.role != "owner":
            raise HTTPException(403, "samo owner mijenja owner ulogu")
        if current == "owner" and body.role != "owner" and _active_owner_count(actor.org_id) <= 1:
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

    # /radnici — account-level worker management (Settings -> Radnici), unlike
    # /workers above which manages only client visibility.
    @app.get("/radnici")
    def radnici_list(actor: Actor = Depends(require_actor_web)):
        from atlas.business import devices as devices_mod
        _require_admin(actor)
        rows = spine.read().execute(
            """SELECT u.id, u.username, mm.role, u.pw_hash FROM memberships mm
               JOIN users u ON u.id = mm.user_id WHERE mm.org_id=? ORDER BY u.username""",
            (actor.org_id,)).fetchall()
        return [{"id": r["id"], "user": r["username"], "role": r["role"],
                 "aktivan": bool(r["pw_hash"]),
                 "device": devices_mod.device_for_worker(spine, r["username"])}
                for r in rows]

    @app.post("/radnici")
    def radnici_add(body: RadnikCreateBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        if body.role not in ("member", "admin"):
            raise HTTPException(400, "nepoznata uloga")
        username = body.username.strip()
        if not username:
            raise HTTPException(400, "korisničko ime je obavezno")
        if spine.read().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise HTTPException(409, "korisnik već postoji")
        sys_role = "admin" if body.role == "admin" else "radnik"
        with spine.write() as c:
            uid = c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,?,?)",
                            (username, None, sys_role)).lastrowid
        tenancy.add_member(spine, actor.org_id, uid, body.role, user=actor.username)
        spine.audit(actor.username, "radnik_add", f"user:{uid}", username)
        return {"id": uid, "user": username, "role": body.role, "aktivan": False, "device": None}

    def _block_last_owner(org_id: int, role: str) -> None:
        """I2: mirrors the last-owner guard from /org/members/{id}/role — resetting/removing
        the last ACTIVE owner would leave the org without any management, and (after C1)
        without anyone allowed to self-activate via /login/activate -> permanent lockout
        without UI recovery. Counts ONLY active ones (pw_hash != NULL) — see _active_owner_count."""
        if role != "owner":
            return
        if _active_owner_count(org_id) <= 1:
            raise HTTPException(409, "zadnji vlasnik organizacije se ne može ukloniti niti resetirati")

    @app.post("/radnici/{radnik_id}/reset")
    def radnici_reset(radnik_id: int, request: Request, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        role = tenancy.role_of(spine, actor.org_id, radnik_id)
        if role is None:
            raise HTTPException(404, "nije član organizacije")
        if role == "owner" and actor.user_id != radnik_id:
            raise HTTPException(403, "vlasnika smije resetirati samo on sam")
        _block_last_owner(actor.org_id, role)
        with spine.write() as c:
            c.execute("UPDATE users SET pw_hash=NULL WHERE id=?", (radnik_id,))
        ip = request.client.host if request.client else "?"
        spine.audit(actor.username, "radnik_reset", f"user:{radnik_id}", f"ip={ip}")
        return {"ok": True}

    @app.delete("/radnici/{radnik_id}")
    def radnici_delete(radnik_id: int, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        role = tenancy.role_of(spine, actor.org_id, radnik_id)
        if role is None:
            raise HTTPException(404, "nije član organizacije")
        if role == "owner" and actor.user_id != radnik_id:
            raise HTTPException(403, "vlasnika smije ukloniti samo on sam")
        _block_last_owner(actor.org_id, role)
        with spine.write() as c:
            c.execute("DELETE FROM memberships WHERE org_id=? AND user_id=?",
                      (actor.org_id, radnik_id))
        spine.audit(actor.username, "radnik_remove", f"user:{radnik_id}")
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
        if not can(spine, actor, asset, action):  # hard org isolation + ACL
            raise HTTPException(404 if actor.org_id != s["org_id"] else 403, "nedozvoljeno")
        return s

    @app.get("/skills")
    def skills_list(status: str | None = None, actor: Actor = Depends(require_actor_web)):
        return skills_mod.readable(skills_mod.list_skills(spine, actor.org_id, status), actor)

    @app.get("/skills/health")
    def skills_health(actor: Actor = Depends(require_actor_web)):
        # insight into catalog health (dead/duplicate/incomplete) — REPORT ONLY, no
        # automatic deletion; the admin decides. Skills are human procedures.
        _require_admin(actor)
        return skills_mod.health(spine, actor.org_id)

    @app.post("/skills")
    def skills_create(body: SkillBody, actor: Actor = Depends(require_actor_web)):
        # a skill = an office procedure; a viewer (read-only) must not author one
        # (otherwise via activation it enters a higher-rank prompt = stored injection; Codex)
        if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["member"]:
            raise HTTPException(403, "za kreiranje vještine potrebna je barem member uloga")
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
        s = _skill_or_404(skill_id, actor, "manage")
        # activating a NON-private skill enters the agent prompt of EVERYONE in the org -> admin+
        # (a private active skill is visible only to its owner; Codex stored-injection)
        if body.status == "active" and (s["visibility"] or "org") != "private" \
                and ROLE_RANK.get(actor.role, 0) < ROLE_RANK["admin"]:
            raise HTTPException(403, "aktivaciju dijeljene vještine odobrava admin/owner")
        try:
            skills_mod.set_status(spine, skill_id, body.status, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    def _answer(query: str, actor: Actor, fresh: bool = False) -> dict:
        try:
            return pipeline.answer(spine, cfg, query, actor.username,
                                   llm=model_settings.build_llm(spine, cfg), fresh=fresh,
                                   actor=actor)
        except (LLMUnavailable, LLMError):
            return {"answer": "LLM trenutno nedostupan ili je vratio grešku.", "lane": "chat",
                    "confidence": 0, "sources": [], "cached": False}

    def _chat_gate(actor: Actor) -> None:
        if not limiter.allow(f"chat:{actor.username}", _CHAT_PER_MIN):
            raise HTTPException(429, "previše upita — pričekajte minutu")

    @app.post("/chat")
    def chat(body: ChatBody, actor: Actor = Depends(require_actor_web)):
        # require_actor_web: accepts both cookie and Bearer — the web UI sends a cookie
        # (E2E finding: require_actor is Bearer-only so web chat is always 401).
        _chat_gate(actor)
        # Agent path only for users allowed to take actions AND only for the free
        # "chat" lane. Deterministic lanes (reject, sql, ocr, greeting...) are
        # handled without an LLM through _answer — otherwise every query would build
        # an LLM client and try the network (expensive + the rate-limit window slips).
        # LLM output is untrusted: the write is NOT executed, only stored as a
        # one-time owner token for /chat/potvrdi.
        if actor.role not in _AGENT_ROLES or router_mod.route(body.q) != "chat":
            return _answer(body.q, actor, fresh=body.fresh)
        llm = model_settings.build_llm(spine, cfg)
        try:
            res = agent.run_agent(spine, cfg, body.q, actor, llm)
        except (LLMUnavailable, LLMError):
            return _answer(body.q, actor, fresh=body.fresh)
        out = {"answer": res["text"], "lane": "agent", "confidence": 1,
               "sources": res["sources"], "cached": False}
        pending = res.get("pending")
        if pending:
            token = agent.stash_pending(spine, actor, pending)
            out["pending"] = {"token": token, "summary": pending["summary"],
                              "risk": pending.get("risk", "med")}
        return out

    @app.post("/chat/potvrdi")
    def chat_potvrdi(body: PendingTokenBody, actor: Actor = Depends(require_actor_web)):
        # double-clicking does not execute twice (atomic consume in confirm_pending);
        # permissions are re-checked in run_tool now, not at proposal time.
        try:
            out = agent.confirm_pending(spine, cfg, body.token, actor, _PENDING_TTL_MIN,
                                        remember=body.remember)
        except ValueError as e:
            msg = str(e)
            raise HTTPException(404 if "ne postoji" in msg else 400, msg) from e
        return {"ok": True, **out}

    @app.post("/chat/odustani")
    def chat_odustani(body: PendingTokenBody, actor: Actor = Depends(require_actor_web)):
        agent.cancel_pending(spine, body.token, actor)
        return {"ok": True}

    # --- Parked actions (an autonomous run prepares, the owner approves) ---
    @app.get("/parkirano")
    def parkirano_list(actor: Actor = Depends(require_actor_web)):
        from atlas.business import parked
        _require_owner(actor)  # approval queue = office owner
        return {"radnje": parked.list_pending(spine, actor.org_id)}

    @app.post("/parkirano/{park_id}/odobri")
    def parkirano_approve(park_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import parked
        _require_owner(actor)
        try:
            return parked.approve(spine, cfg, park_id, actor)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/parkirano/{park_id}/odbaci")
    def parkirano_reject(park_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import parked
        _require_owner(actor)
        return {"ok": parked.reject(spine, park_id, actor)}

    # --- Approval grants ("confirm once, remember"; high-risk NEVER auto) ---
    @app.get("/grants")
    def grants_list(actor: Actor = Depends(require_actor_web)):
        from atlas.business import agent_grants
        return {"grants": agent_grants.list_grants(spine, actor)}

    @app.post("/grants")
    def grants_create(body: GrantBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import agent_grants
        if body.scope == "org":  # a rule for the whole office = owner
            _require_owner(actor)
        try:
            gid = agent_grants.create_grant(spine, actor, body.tool, body.args,
                                            scope=body.scope, days=body.days, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": gid}

    @app.delete("/grants/{grant_id}")
    def grants_revoke(grant_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import agent_grants
        try:
            ok = agent_grants.revoke_grant(spine, actor, grant_id,
                                           is_owner=(actor.role == "owner"), user=actor.username)
        except ValueError as e:
            raise HTTPException(403, str(e)) from e
        return {"ok": ok}

    # --- Power (UPS/NUT + ordered shutdown) — all admin-only ---
    @app.get("/napajanje/config")
    def napajanje_config_get(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        return power_mod.get_config(spine)

    @app.post("/napajanje/config")
    def napajanje_config_set(body: PowerConfigBody, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)
        fields = body.model_dump(exclude_none=True)
        before = power_mod.get_config(spine)
        try:
            after = power_mod.save_config(spine, **fields)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        changed = {k: after[k] for k in fields if before.get(k) != after.get(k)}
        if changed:  # every change to the shutdown conditions goes to audit
            spine.audit(actor.username, "napajanje_config", "", str(changed))
        return after

    @app.get("/napajanje/status")
    def napajanje_status(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        pc = power_mod.get_config(spine)
        st = power_mod.read_status(pc["nut_host"], pc["nut_port"], pc["ups_name"])
        st["flags"] = sorted(st.get("flags", []))  # set -> JSON list
        return st

    @app.get("/napajanje/plan")
    def napajanje_plan(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        return power_mod.shutdown_plan(spine)  # dry-run: just a preview of the order

    @app.post("/napajanje/arm")
    def napajanje_arm(body: ArmBody, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)
        power_mod.save_config(spine, armed=body.armed)
        spine.audit(actor.username, "napajanje_arm", "", "1" if body.armed else "0")
        return {"armed": body.armed}

    # --- Worker fleet: agent protocol + token issuing + commands ---
    def _require_device(request: Request) -> int:
        """Auth of the worker agent via a device token (outbound connection, no JWT)."""
        did = fleet.verify_token(spine, request.headers.get("Authorization", ""))
        if did is None:
            raise HTTPException(401, "nevažeći token uređaja")
        return did

    @app.get("/agent/poll")
    def agent_poll(request: Request):
        did = _require_device(request)
        cmd = fleet.next_command(spine, did)
        if cmd is None:
            return Response(status_code=204)
        cmd["sig"] = fleet.sign_command(spine, did, cmd, cfg)  # per-device signature; the agent verifies
        return cmd

    @app.post("/agent/result")
    def agent_result(body: AgentResultBody, request: Request):
        did = _require_device(request)
        # Close ONLY your own command that is 'in_progress' — otherwise an agent
        # could skip execution or overwrite an already-finished result.
        if not fleet.complete(spine, body.id, did, {"ok": body.ok, "detail": body.detail}):
            raise HTTPException(404, "naredba nije aktivna za ovaj uređaj")
        spine.audit("agent", "agent_result", f"device:{did}",
                    f"cmd:{body.id}:{'ok' if body.ok else 'fail'}")
        return {"ok": True}

    # --- agent self-enrollment (no manual token): announce -> owner approves -> pick up ---
    def _require_tls_enroll(request: Request) -> None:
        # enrollment carries a secret/credentials -> not over cleartext http when
        # https is required (a MITM on the LAN would steal the secret; Codex finding).
        # We trust X-Forwarded-Proto ONLY when behind a configured proxy — otherwise
        # anyone could send it to bypass the check (Codex finding).
        scheme = request.url.scheme
        # enable trust_proxy ONLY when direct external access to the backend is
        # blocked (the proxy is the only way in) — otherwise anyone sends X-Forwarded-Proto (Codex #2)
        if getattr(cfg, "trust_proxy", False):
            scheme = request.headers.get("x-forwarded-proto") or scheme
        if getattr(cfg, "https_only", False) and scheme != "https":
            raise HTTPException(400, "upis agenta samo preko https")

    # --- Office rules (owner types them; always in the agent prompt) ---
    # NOTE: ured_pravila live in config_overrides (module=agent) which — like ALL
    # other settings (model/power/fleet) — is installation-global, not per org.
    # ATLAS is a single office; multi-tenant org-scoping of config_overrides is a
    # pre-existing larger change. _require_owner is the same gate as power/enrollment/scheduler.
    @app.get("/ured-pravila")
    def ured_pravila_get(actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)  # rules are office configuration -> owner (Codex)
        return {"pravila": agent.get_ured_pravila(spine)}

    @app.post("/ured-pravila")
    def ured_pravila_set(body: UredPravilaBody, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)
        agent.set_ured_pravila(spine, body.pravila, user=actor.username)
        return {"ok": True}

    # --- Agent budget shield (daily cap; protects against cost-runaway of autonomous runs) ---
    @app.get("/budget")
    def budget_get(actor: Actor = Depends(require_actor_web)):
        from atlas.business import agent_budget
        _require_admin(actor)  # spend = operational insight (admin)
        return {"budget": agent_budget.usage_today(spine)}

    @app.post("/budget")
    def budget_set(body: BudgetBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import agent_budget
        _require_owner(actor)  # caps = office configuration (owner)
        for k, v in body.caps.items():
            if k in agent_budget.DEFAULTS:
                spine.set_override("agent", f"budget_{k}", max(0, int(v)))
        spine.audit(actor.username, "budget_set", "agent")
        return {"ok": True, "budget": agent_budget.usage_today(spine)}

    # --- Replay: repeat a previously executed agent action from the audit trail (owner) ---
    @app.get("/replay")
    def replay_list(actor: Actor = Depends(require_actor_web)):
        from atlas.business import replay
        _require_owner(actor)  # repeating actions = owner operation
        return {"radnje": replay.list_replayable(spine)}

    @app.post("/replay/{audit_id}")
    def replay_run(audit_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import replay
        _require_owner(actor)
        try:
            return replay.replay(spine, cfg, audit_id, actor)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # --- Scheduled tasks (owner; action from an allowlist, no arbitrary code) ---
    @app.get("/zakazano")
    def zakazano_list(actor: Actor = Depends(require_actor_web)):
        from atlas.business import scheduler_tasks
        _require_owner(actor)  # tasks carry messages/schedules -> owner only (Codex)
        return {"zadaci": scheduler_tasks.list_tasks(spine, actor.org_id),
                "akcije": scheduler_tasks.action_labels()}

    @app.post("/zakazano")
    def zakazano_create(body: ScheduledTaskBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import scheduler_tasks
        _require_owner(actor)
        try:
            tid = scheduler_tasks.create_task(spine, actor.org_id, body.title, body.action_key,
                                              body.params, body.day_of_month, body.hour,
                                              user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        spine.audit(actor.username, "scheduled_create", f"task:{tid}:{body.action_key}")
        return {"id": tid}

    @app.post("/zakazano/{task_id}/toggle")
    def zakazano_toggle(task_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import scheduler_tasks
        _require_owner(actor)
        rows = scheduler_tasks.list_tasks(spine, actor.org_id)
        cur = next((t for t in rows if t["id"] == task_id), None)
        if cur is None:
            raise HTTPException(404, "nepoznat zadatak")
        scheduler_tasks.set_enabled(spine, task_id, actor.org_id, not cur["enabled"])
        return {"enabled": not cur["enabled"]}

    @app.delete("/zakazano/{task_id}")
    def zakazano_delete(task_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import scheduler_tasks
        _require_owner(actor)
        scheduler_tasks.delete_task(spine, task_id, actor.org_id)
        return {"ok": True}

    @app.post("/uredjaji/enrollments/open")
    def uredjaj_enroll_open(actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)  # only the owner opens pairing (fail-closed by default)
        until = fleet.open_enrollment(spine, actor.username)
        return {"open_until": until}

    @app.post("/agent/enroll")
    def agent_enroll(body: EnrollBody, request: Request):
        _require_tls_enroll(request)
        ip = request.client.host if request.client else "?"
        if not limiter.allow(f"enroll:{ip}", limit=10, window_s=60.0):
            raise HTTPException(429, "previše zahtjeva za upis — pričekajte")
        try:
            enroll_id, secret = fleet.enroll_request(spine, body.name or "", source=ip)
        except ValueError as e:
            # closed pairing -> 403 (fail-closed); cap on the waiting queue -> 429
            code = 403 if "sparivanje" in str(e) else 429
            raise HTTPException(code, str(e)) from e
        return {"enroll_id": enroll_id, "secret": secret}

    @app.get("/agent/enroll/{enroll_id}")
    def agent_enroll_poll(enroll_id: str, request: Request):
        _require_tls_enroll(request)
        secret = request.headers.get("X-Enroll-Secret", "")
        try:
            return fleet.poll_enrollment(spine, cfg, enroll_id, secret)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/uredjaji/enrollments")
    def uredjaj_enrollments(actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)
        return fleet.list_pending_enrollments(spine)

    @app.post("/uredjaji/enrollments/{enroll_id}/approve")
    def uredjaj_enroll_approve(enroll_id: str, body: ApproveBody,
                               actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)
        try:
            device_id = fleet.approve_enrollment(spine, cfg, enroll_id, body.device_name,
                                                 actor.username)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"device_id": device_id}

    @app.get("/uredjaji/{device_id}/aktivnost")
    def uredjaj_aktivnost(device_id: int, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)  # fleet monitoring = owner; the UI polls for a "live" view
        return {"aktivnost": fleet.device_activity(spine, device_id)}

    @app.post("/uredjaji/{device_id}/token")
    def uredjaj_token_issue(device_id: int, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)  # token issuing = machine-level, owner-only
        try:
            token = fleet.issue_token(spine, device_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        spine.audit(actor.username, "device_token_issue", f"device:{device_id}")
        # the PER-DEVICE sign_key ships with the token (the agent verifies signatures
        # with it) — the master never leaves the server; it is delivered at install
        # time, separately from the runtime
        return {"token": token, "sign_key": fleet.device_sign_key(spine, device_id, cfg)}

    @app.delete("/uredjaji/{device_id}/token")
    def uredjaj_token_revoke(device_id: int, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)
        fleet.revoke_token(spine, device_id)
        spine.audit(actor.username, "device_token_revoke", f"device:{device_id}")
        return {"ok": True}

    @app.post("/uredjaji/{device_id}/naredba")
    def uredjaj_naredba(device_id: int, body: NaredbaBody,
                        actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)  # daily start/stop = admin+ (with audit)
        from atlas.business import devices as devices_mod
        if device_id not in {d["id"] for d in devices_mod.list_devices(spine)}:
            raise HTTPException(404, "nepoznat uređaj")
        try:
            cid = fleet.enqueue(spine, device_id, body.action, body.program_key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        spine.audit(actor.username, "agent_naredba", f"device:{device_id}",
                    f"{body.action}:{body.program_key or ''}")
        return {"id": cid}

    @app.get("/fleet/programi")
    def fleet_programi_list(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)  # reading the allowlist admin+
        return fleet.list_programs(spine)

    @app.post("/fleet/programi")
    def fleet_programi_add(body: ProgramBody, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)  # defining WHAT it may ever run = security policy
        try:
            key = fleet.add_program(spine, body.key, body.label, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        spine.audit(actor.username, "fleet_program_add", key)
        return {"key": key}

    @app.delete("/fleet/programi/{key}")
    def fleet_programi_remove(key: str, actor: Actor = Depends(require_actor_web)):
        _require_owner(actor)
        fleet.remove_program(spine, key)
        spine.audit(actor.username, "fleet_program_remove", key)
        return {"ok": True}

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
            "model": cfg.llm_model or "atlas",
            "usage": {},
        }

    # require_user_web: accepts both cookie (UI) and Bearer (API clients)
    @app.post("/watchlist/run")
    def watchlist_run(user: str = Depends(require_user_web)):
        return [dataclasses.asdict(c) for c in watchlist.check_all(spine, cfg)]

    @app.get("/watchlist/sources")
    def watchlist_list_sources(actor: Actor = Depends(require_actor_web)):
        rows = spine.read().execute("SELECT * FROM watch_sources").fetchall()
        # a client-linked source doesn't leak to a restricted worker (law/RSS = NULL stays)
        return _visible_rows(actor, [dict(r) for r in rows])

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
    def dashboard_json(actor: Actor = Depends(require_actor_web)):
        # a restricted worker doesn't see obligations/expiry/missing-documents of others' clients
        visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        return dashboard.home_data(spine, visible=visible)

    @app.post("/expiry/{item_id}/podsjetnik")
    def expiry_reminder(item_id: int, actor: Actor = Depends(require_actor_web)):
        if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["member"]:
            raise HTTPException(403, "potrebna barem članska uloga")
        row = spine.read().execute(
            "SELECT client_id FROM expiry_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznata stavka isteka")
        if row["client_id"] is not None:
            _guard_client(actor, row["client_id"])  # anti-IDOR
        try:
            res = expiry_mod.send_expiry_reminder(spine, cfg, item_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        spine.audit(actor.username, "expiry_reminder", f"item:{item_id}", res.get("status", ""))
        return res

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
        from atlas.web.templates_uvoz import uvoz_page
        return uvoz_page()

    @app.get("/ui/novi-klijent", response_class=HTMLResponse)
    def ui_novi_klijent(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_wizard import wizard_page
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
        from atlas.web.templates_pracenje import pracenje_page
        return pracenje_page()

    @app.get("/ui/uredjaji", response_class=HTMLResponse)
    def ui_uredjaji(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_devices import devices_page
        return devices_page()

    # --- PWA: wraps the web UI so it installs as an application (no new screens) ---
    @app.get("/manifest.webmanifest")
    def pwa_manifest():
        return Response(content=_PWA_MANIFEST, media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/sw.js")
    def pwa_service_worker():
        # SW at the root -> scope '/'; no-cache because the SW must refresh immediately
        return Response(content=_PWA_SW_JS, media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/offline.html", response_class=HTMLResponse)
    def pwa_offline():
        return _PWA_OFFLINE_HTML

    @app.get("/export/{token}")
    def export_download(token: str, actor: Actor = Depends(require_actor_web)):
        from atlas.business import excel_export
        path = excel_export.path_for(cfg, token)
        if path is None:
            raise HTTPException(404, "izvoz ne postoji ili je istekao")
        return FileResponse(path, filename="atlas-izvoz.xlsx",
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/obveze/aktivnost")
    def obveze_aktivnost(since: str | None = None, worker: str | None = None,
                         actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)  # who-does-what is monitoring insight
        if worker:
            return obveze.worker_closed(spine, worker, since=since)
        return obveze.worker_activity(spine, since=since)

    # --- user-defined obligation fields (registry + JSON meta, core locked) ---
    @app.get("/obveze/polja")
    def obveze_polja_list(actor: Actor = Depends(require_actor_web)):
        # reading field definitions = any logged-in user (a worker needs to know the
        # columns to fill a value / render the table); defining is owner-only
        from atlas.business import obligation_fields
        return obligation_fields.list_fields(spine)

    @app.post("/obveze/polja")
    def obveze_polja_add(body: ObligationFieldBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import obligation_fields
        _require_owner(actor)  # defining "columns" = structural policy
        try:
            key = obligation_fields.add_field(spine, body.label, body.type, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"key": key}

    @app.delete("/obveze/polja/{key}")
    def obveze_polja_remove(key: str, actor: Actor = Depends(require_actor_web)):
        from atlas.business import obligation_fields
        _require_owner(actor)
        obligation_fields.remove_field(spine, key, user=actor.username)
        return {"ok": True}

    @app.post("/obveze/{obligation_id}/polje")
    def obveze_polje_set(obligation_id: int, body: FieldValueBody,
                         actor: Actor = Depends(require_actor_web)):
        from atlas.business import obligation_fields
        # filling values = everyday work (member+); defining is owner
        if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["member"]:
            raise HTTPException(403, "potrebna barem članska uloga")
        row = spine.read().execute(
            "SELECT client_id FROM obligations WHERE id=?", (obligation_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznata obveza")
        if row["client_id"] is not None:  # obligation linked to a client -> check visibility (anti-IDOR)
            _guard_client(actor, row["client_id"])
        try:
            meta = obligation_fields.set_value(spine, obligation_id, body.key, body.value,
                                               user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"obligation_id": obligation_id, "meta": meta}

    @app.get("/ui/obveze-polja", response_class=HTMLResponse)
    def ui_obveze_polja(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_ui import obveze_polja_page
        return obveze_polja_page()

    @app.get("/ui/obveze-aktivnost", response_class=HTMLResponse)
    def ui_obveze_aktivnost(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_ui import obveze_aktivnost_page
        return obveze_aktivnost_page()

    @app.get("/postavi-agent", response_class=HTMLResponse)
    def ui_postavi_agent(request: Request):
        try:
            actor = require_actor_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        _require_owner(actor)  # agent issuing = machine-level, owner-only
        from atlas.web.templates_ui import postavi_agent_page
        return postavi_agent_page()

    @app.get("/ui/napajanje", response_class=HTMLResponse)
    def ui_napajanje(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_ui import napajanje_page
        return napajanje_page()

    @app.get("/ui/posta", response_class=HTMLResponse)
    def ui_posta(request: Request):
        try:
            _require_admin(require_actor_web(request))
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_connectors import connectors_page
        return connectors_page()

    @app.get("/connector-types")
    def connector_types(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.business import connectors as cx
        return cx.list_types()

    @app.get("/connectors")
    def connectors_list(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.business import connectors as cx
        return cx.list_connectors(spine, org_id=actor.org_id)

    @app.post("/connectors/test")
    def connectors_test(body: ConnectorTestBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.business import connectors as cx
        try:
            return cx.test_draft(body.kind, body.config)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/connectors")
    def connectors_create(body: ConnectorCreateBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.business import connectors as cx
        try:
            return cx.create(spine, body.kind, body.name, body.config,
                             cfg=cfg, org_id=actor.org_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/connectors/{cid}/status")
    def connectors_status(cid: int, body: ConnectorStatusBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.business import connectors as cx
        try:
            cx.set_status(spine, cid, body.status, org_id=actor.org_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": cid, "status": body.status}

    @app.delete("/connectors/{cid}")
    def connectors_delete(cid: int, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.business import connectors as cx
        cx.delete(spine, cid, org_id=actor.org_id, user=actor.username)
        return {"id": cid, "removed": True}

    @app.post("/telegram/pairing")
    def telegram_pairing(actor: Actor = Depends(require_actor_web)):
        # self-service: the token binds Telegram to THIS user (not admin for someone else —
        # otherwise the worker gets the admin's permissions). Each logged-in user has their own token.
        from atlas.business.telegram_gateway import create_pairing_token
        token = create_pairing_token(spine, actor.user_id, actor.org_id)
        return {"token": token, "command": f"/start {token}"}

    @app.get("/ui/backup", response_class=HTMLResponse)
    def ui_backup(request: Request):
        try:
            _require_admin(require_actor_web(request))
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_backup import backup_page
        return backup_page()

    @app.get("/backup/list")
    def backup_list(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.ops import backup
        return backup.list_backups(cfg)

    @app.post("/backup")
    def backup_create(actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.ops import backup
        b = backup.create_backup(cfg)
        backup.prune(cfg, keep=14)
        spine.audit(actor.username, "backup_create", b["name"])
        return b

    @app.get("/backup/download/{name}")
    def backup_download(name: str, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)
        from atlas.ops import backup
        try:
            path = backup.resolve_backup(cfg, name)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return FileResponse(path, media_type="application/octet-stream",
                            filename=os.path.basename(path))

    @app.get("/ui/racunalo", response_class=HTMLResponse)
    def ui_racunalo(request: Request):
        # system state + software inventory = admin (not every worker)
        try:
            _require_admin(require_actor_web(request))
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_preflight import preflight_page
        return preflight_page()

    @app.get("/preflight")
    def preflight_summary(request: Request):
        from atlas.web import firstrun
        from atlas.ops import preflight
        if not firstrun.needs_onboarding(spine):
            _require_admin(require_actor_web(request))  # nakon setupa: admin-only, puni prikaz
            return preflight.summary(cfg)
        # anonymous onboarding: rate-limit + reduced (without exact paths/OS/GPU
        # details a LAN observer would collect) — Codex finding
        ip = request.client.host if request.client else "?"
        if not limiter.allow(f"preflight:{ip}", limit=20, window_s=60.0):
            raise HTTPException(429, "previše zahtjeva — pričekajte minutu")
        return preflight.summary(cfg, reduced=True)

    @app.get("/ui/setup", response_class=HTMLResponse)
    def ui_setup(request: Request):
        from atlas.web import firstrun
        if not firstrun.needs_onboarding(spine):
            return RedirectResponse("/login", status_code=303)  # setup gotov
        from atlas.web.templates_setup import setup_page
        return setup_page()

    @app.post("/setup/owner")
    def setup_owner(body: SetupOwnerBody, request: Request):
        from atlas.web import firstrun
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
        from atlas.web.templates_arhitektura import arhitektura_page
        return arhitektura_page()

    @app.get("/ui/dok-tipovi", response_class=HTMLResponse)
    def ui_dok_tipovi(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        from atlas.web.templates_doctypes import doctypes_page
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

    @app.get("/ui/vidljivost", response_class=HTMLResponse)
    def ui_vidljivost(request: Request):
        try:
            require_user_web(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return vidljivost_page()

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
        # Codex finding #7: changing the LLM endpoint is an exfiltration vector
        # (any worker redirects the "brain" to someone else's server and leaks all
        # queries) — only the admin/office owner may do it.
        _require_admin(actor)
        try:
            return model_settings.save(spine, body.provider, body.model, body.base_url,
                                       body.api_key, body.embed_model, body.ollama_url,
                                       actor.username, cfg=cfg)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/model/test")
    def model_test(user: str = Depends(require_user_web)):
        return model_settings.test_connection(spine, cfg)

    @app.get("/model/fallbacks")
    def model_fallbacks_get(user: str = Depends(require_user_web)):
        return {"fallbacks": model_settings.get_fallbacks(spine)}

    @app.post("/model/fallbacks")
    def model_fallbacks_set(body: FallbacksBody, actor: Actor = Depends(require_actor_web)):
        # same exfiltration vector as the primary model -> admin/owner
        _require_admin(actor)
        try:
            model_settings.set_fallbacks(
                spine, [p.model_dump() for p in body.profiles], cfg, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "count": len(body.profiles)}

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
        from atlas.business import folder_sync
        return folder_sync.sync_all(spine, cfg)

    # only admin/owner changes the folder registry — role='klijenti' redirects
    # where onboarding WRITES, so an ordinary worker must not re-register folders
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
        from atlas.business import folder_scan as fs
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
        from atlas.business import folder_scan as fs
        return fs.latest(spine, folder_id) or {}

    @app.post("/folders/{folder_id}/ocr")
    def folder_ocr(folder_id: int, user: str = Depends(require_user_web)):
        from atlas.docs import ocr as ocr_mod
        row = spine.read().execute("SELECT path, label FROM folders WHERE id=?",
                                   (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "nepoznata mapa")
        base = folders_mod._scoped(cfg, row["path"])
        res = ocr_mod.bulk_ocr(spine, cfg, base)
        from atlas.business import folder_scan as fs
        fs.scan(spine, cfg, folder_id)  # refresh the numbers — the button must not stay stale
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
        from atlas.docs import ocr as ocr_mod
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
            actor = require_actor_web(request)
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
        rows = _visible_rows(actor, obveze.list_period(spine, kind, period))
        return render_obveze(kind, period, rows, tabs)

    @app.get("/obveze.json")
    def obveze_json(kind: str = "PDV", period: str | None = None,
                     actor: Actor = Depends(require_actor_web)):
        if obveze.get_type(spine, kind) is None:
            raise HTTPException(400, f"nepoznat kind: {kind!r}")
        period = period or date.today().strftime("%Y-%m")
        _require_valid_period(period)
        # a restricted worker doesn't see obligations (nor meta) of others' clients
        return _visible_rows(actor, obveze.list_period(spine, kind, period))

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
        from atlas.docs import extraction as extraction_mod
        # a worker must not extract someone else's document nor assign it to another's client
        drow = spine.read().execute("SELECT client_id FROM documents WHERE id=?",
                                    (body.doc_id,)).fetchone()
        if drow is not None and drow["client_id"] is not None:
            _guard_client(actor, drow["client_id"])
        if body.client_id is not None:
            _guard_client(actor, body.client_id)
        # the LLM is a fallback — without a configured provider extraction goes regex-only
        try:
            llm = model_settings.build_llm(spine, cfg)
        except Exception:
            llm = None
        try:
            return extraction_mod.extract(spine, cfg, body.doc_id, body.doc_type,
                                          llm=llm, client_id=body.client_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # Devices (E): the admin changes the registry; scan/print is used by every
    # worker (with device selection at action time).
    @app.get("/devices")
    def devices_list(kind: str | None = None, actor: Actor = Depends(require_actor_web)):
        from atlas.business import devices as devices_mod
        return devices_mod.list_devices(spine, kind=kind)

    @app.get("/devices/discover")
    def devices_discover(actor: Actor = Depends(require_actor_web)):
        from atlas.core import lan
        _require_admin(actor)
        return lan.discover()

    @app.post("/devices")
    def devices_add(body: DeviceBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import devices as devices_mod
        from atlas.core import lan
        _require_admin(actor)
        try:
            return devices_mod.add_device(
                spine, body.kind, body.name, body.url, user=actor.username,
                host=body.host, mac=body.mac, worker_username=body.worker_username,
                caps=body.caps.model_dump() if body.caps else None)
        except (ValueError, lan.LanBlocked) as e:
            raise HTTPException(400, str(e)) from e

    @app.patch("/devices/{device_id}")
    def devices_update(device_id: int, body: DeviceUpdateBody,
                       actor: Actor = Depends(require_actor_web)):
        from atlas.business import devices as devices_mod
        from atlas.core import lan
        _require_admin(actor)
        if spine.read().execute("SELECT 1 FROM devices WHERE id=?",
                                (device_id,)).fetchone() is None:
            raise HTTPException(404, f"nepoznat uređaj: {device_id}")
        fields = body.model_dump(exclude_unset=True)
        if "caps" in fields and fields["caps"] is not None:
            fields["caps"] = body.caps.model_dump()
        try:
            return devices_mod.update_device(spine, device_id, user=actor.username, **fields)
        except (ValueError, lan.LanBlocked) as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/devices/{device_id}")
    def devices_delete(device_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import devices as devices_mod
        _require_admin(actor)
        try:
            devices_mod.remove_device(spine, device_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"id": device_id, "removed": True}

    @app.post("/devices/{device_id}/scan")
    def devices_scan(device_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import devices as devices_mod
        from atlas.core import lan
        try:
            return devices_mod.scan(spine, cfg, device_id, user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except (lan.LanBlocked, RuntimeError) as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/devices/{device_id}/print")
    def devices_print(device_id: int, body: PrintBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import devices as devices_mod
        from atlas.core import lan
        row = spine.read().execute("SELECT client_id FROM documents WHERE id=?",
                                   (body.doc_id,)).fetchone()
        if row is None:
            raise HTTPException(400, "nepoznat dokument")
        if row["client_id"] is not None:
            _guard_client(actor, row["client_id"])  # a worker doesn't print others' clients
        try:
            return devices_mod.print_doc(spine, cfg, device_id, body.doc_id,
                                         user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except (lan.LanBlocked, RuntimeError) as e:
            raise HTTPException(502, str(e)) from e

    # Folder architecture (D2): agreeing on the office structure = admin/owner work.
    # The preview lists ALL clients from disk (the KLIJENTI folder) so it is not
    # for restricted workers.
    @app.get("/folder-architecture")
    def folder_architecture_preview(actor: Actor = Depends(require_actor_web)):
        from atlas.business import folder_architecture as fa
        _require_admin(actor)
        try:
            return fa.propose(spine, cfg)
        except ValueError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/folder-architecture/learned")
    def folder_architecture_learned(actor: Actor = Depends(require_actor_web)):
        from atlas.business import folder_architecture as fa
        _require_admin(actor)
        try:
            return fa.learn_structure(spine, cfg)
        except ValueError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/folder-architecture/template")
    def folder_architecture_template_get(actor: Actor = Depends(require_actor_web)):
        from atlas.business import folder_architecture as fa
        _require_admin(actor)
        return fa.get_template(spine)

    @app.post("/folder-architecture/template")
    def folder_architecture_template_set(body: ArchTemplateBody,
                                         actor: Actor = Depends(require_actor_web)):
        from atlas.business import folder_architecture as fa
        _require_admin(actor)
        try:
            return fa.set_template(spine, office=body.office,
                                   client_subdirs=body.client_subdirs,
                                   user=actor.username)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/folder-architecture/apply")
    def folder_architecture_apply(actor: Actor = Depends(require_actor_web)):
        from atlas.business import folder_architecture as fa
        _require_admin(actor)  # creates folders for ALL clients — admin/owner only
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
        # only the passed fields change; omitted ones stay untouched
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
    def checklist_worst(actor: Actor = Depends(require_actor_web)):
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        return checklist.worst_first(spine, visible=vis)

    @app.get("/notes")
    def notes_search(client_id: int | None = None, q: str | None = None,
                      actor: Actor = Depends(require_actor_web)):
        if client_id is not None:
            _guard_client(actor, client_id)
        rows = [dict(r) for r in notes.search(spine, term=q, client_id=client_id)]
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        if vis is not None:  # a restricted worker doesn't see notes of others' clients
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
        _guard_client(actor, body.client_id)  # a worker doesn't send messages to others' clients
        return messaging.send_to_client(spine, cfg, body.client_id, body.subject, body.body,
                                         dry_run=body.dry_run)

    @app.post("/messaging/campaign")
    def messaging_campaign(body: MessagingCampaignBody, actor: Actor = Depends(require_actor_web)):
        _require_admin(actor)  # mass outbound to all clients = admin work
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
        if vis is not None:  # a restricted worker doesn't see the log of others' clients
            out = [r for r in out if r.get("client_id") in vis]
        return out

    def _guard_client(actor: Actor, client_id: int) -> None:
        if not client_visibility.can_see(spine, actor.user_id, client_id, actor.role):
            raise HTTPException(403, "nemate pristup ovom klijentu")

    def _visible_rows(actor: Actor, rows: list) -> list:
        """Drop rows of hidden clients (client_id IS NULL = office-wide, stays for
        everyone). Shared filter for aggregate read-lists (expiry, notifications)."""
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        if vis is None:
            return rows
        return [r for r in rows if r.get("client_id") is None or r.get("client_id") in vis]

    @app.post("/clients/assist")
    def client_assist(body: ClientAssistBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import client_assist
        # server-side throttle — the client debounce can be bypassed, the LLM costs money
        if not limiter.allow(f"assist:{actor.user_id}", limit=20, window_s=60.0):
            raise HTTPException(429, "previše assist zahtjeva — uspori tipkanje")
        try:
            llm = model_settings.build_llm(spine, cfg)
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
        # otherwise a restricted creator wouldn't see their own creation
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        if vis is not None:
            client_visibility.grant(spine, actor.user_id, result["id"], actor.username)
        return {"id": result["id"], "nas_folder": result["nas_folder"]}

    @app.get("/clients/discover")
    def clients_discover(folder_id: int, actor: Actor = Depends(require_actor_web)):
        from atlas.business import client_discovery
        _require_admin(actor)  # enumeration/creation of clients from the NAS folder = admin
        try:
            return client_discovery.discover(spine, cfg, folder_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/clients/discover/commit")
    def clients_discover_commit(body: DiscoverCommitBody, actor: Actor = Depends(require_actor_web)):
        from atlas.business import client_discovery
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
        from atlas.business import secretbox
        # target carries a password -> encrypt in the DB (a backup doesn't leak the
        # credential); the scheme was already validated on the plaintext above
        stored_target = secretbox.encrypt(body.target, cfg) if body.target else body.target
        with spine.write() as c:
            c.execute(
                "UPDATE clients SET messaging_consent=?, messaging_channel=?, messaging_target=? WHERE id=?",
                (body.consent, body.channel, stored_target, client_id),
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
    def dashboard_stats(actor: Actor = Depends(require_actor_web)):
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        return dashboard.stats(spine, visible=vis)

    @app.get("/models/recommend")
    def models_recommend(user: str = Depends(require_user_web)):
        return model_recommender.recommend()

    @app.get("/models/litellm")
    def models_litellm(user: str = Depends(require_user_web)):
        return Response(model_recommender.litellm_config(model_recommender.recommend()),
                         media_type="text/plain")

    @app.get("/monthly")
    def monthly_overview(period: str | None = None, actor: Actor = Depends(require_actor_web)):
        period = period or date.today().strftime("%Y-%m")
        vis = client_visibility.visible_ids(spine, actor.user_id, actor.role)
        ov = monthly.overview(spine, period, visible=vis)
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
            text = translate_mod.translate(model_settings.build_llm(spine, cfg), body.text, body.target)
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
        _require_admin(actor)  # the audit trail reveals others' actions — not for every member
        # Codex finding: without an org filter, an admin of org A sees the audit of org B
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

    def _guard_sop(actor: Actor, sop_id: int) -> None:
        # a SOP linked to a client must not be revealed to a restricted worker
        r = spine.read().execute("SELECT client_id FROM sop_pages WHERE id=?", (sop_id,)).fetchone()
        if r is not None and r["client_id"] is not None:
            _guard_client(actor, r["client_id"])

    @app.get("/sop/pending")
    def sop_pending(actor: Actor = Depends(require_actor_web)):
        return {"items": _visible_rows(actor, sop_mod.list_pending(spine)),
                "summary": sop_mod.editorial_summary(spine)}

    @app.get("/sop/{sop_id}")
    def sop_get(sop_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_sop(actor, sop_id)
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
    def sop_image_list(sop_id: int, actor: Actor = Depends(require_actor_web)):
        _guard_sop(actor, sop_id)
        return sop_images.list_images(spine, sop_id)

    @app.get("/sop/image/{image_id}")
    def sop_image_get(image_id: int, actor: Actor = Depends(require_actor_web)):
        r = spine.read().execute("SELECT sop_id FROM sop_images WHERE id=?", (image_id,)).fetchone()
        if r is not None:
            _guard_sop(actor, r["sop_id"])  # the image inherits the visibility of its SOP/client
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

    # Deterministic lanes that change state IMMEDIATELY (without agent confirmation)
    # must not be triggered from the phone — otherwise "learn from <URL>" / "at Ana's
    # open X" would execute a write without confirmation (Codex finding).
    _TG_SIDE_EFFECT_LANES = ("learn", "flota", "arhitektura")

    def _telegram_answer(query: str, actor: Actor) -> dict:
        """Telegram through the AGENT (the phone gets READ actions). A write (agent pending
        OR a side-effect lane) is NOT executed over Telegram — bounced to confirmation."""
        _chat_gate(actor)
        lane = router_mod.route(query)
        if lane in _TG_SIDE_EFFECT_LANES:
            return {"answer": "⚠ Radnje koje mijenjaju podatke (učenje s weba, "
                    "pokretanje programa, dogovor mapa) napravi i potvrdi u ATLAS aplikaciji."}
        if actor.role not in _AGENT_ROLES or lane != "chat":
            return _answer(query, actor)
        try:
            res = agent.run_agent(spine, cfg, query, actor,
                                  model_settings.build_llm(spine, cfg))
        except (LLMUnavailable, LLMError):
            return _answer(query, actor)
        from atlas.business import telegram_gateway as _tgw
        out = {"answer": _tgw.format_agent_reply(res), "sources": res.get("sources", [])}
        pending = res.get("pending")
        if pending:  # the write is confirmed with inline buttons on Telegram (owner token)
            out["pending_token"] = agent.stash_pending(spine, actor, pending)
        return out

    def _maybe_start_telegram():
        """If the telegram_gateway connector is enabled, start the poll thread. Without
        a configured connector (e.g. in tests) it starts nothing."""
        import threading
        from atlas.business import connectors as cx
        from atlas.business import telegram_gateway as tgw
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
            target=tgw.poll_loop, args=(spine, cfg, token, _telegram_answer, stop),
            kwargs={"limiter": limiter, "key": f"c{row['id']}"},  # unique offset per connector
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
