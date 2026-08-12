# Tool registry for the agentic chat layer (Phase 3). Each tool calls the
# EXISTING business layer — this module only: describes the schema for the LLM,
# validates arguments, checks permissions (role + client visibility) and forwards
# the call.
#
# LLM output is UNTRUSTED: the tool list is fixed (allowlist), arguments are
# validated in code BEFORE every call, unknown/disallowed tool -> ValueError.
#
# ponytail: pretrazi() does not receive an llm (run_tool does not forward one) so
# it does not call pipeline.answer (which without an llm on the chat lane returns
# "LLM nedostupan") — it returns raw hits (retrieval.search + websearch.ddg) which
# the agentic loop (T3) hands to the LLM to compose an answer. Upgrade path: forward
# llm through run_tool if pretrazi ever needs to compose a summary itself.
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

from atlas.business import client_visibility, expiry, client_card, notes, obligations, onboarding
from atlas.core import security
from atlas.rag import retrieval
from atlas.web import websearch

_EDITABLE_FIELDS = {
    "name", "email", "phone", "industry", "pdv_status", "pdv_freq",
    "regime", "legal_form", "has_employees",
}


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    readonly: bool
    min_role: str
    run: Callable  # (spine, cfg, actor, args) -> dict


# --- helper: client by key (oib > id > name) ------------------------

def _resolve_client(spine, key: str) -> dict | None:
    key = (key or "").strip()
    if not key:
        return None
    row = None
    if re.fullmatch(r"\d{11}", key):
        row = spine.read().execute("SELECT * FROM clients WHERE oib=?", (key,)).fetchone()
    if row is None and key.isdigit():
        row = spine.read().execute("SELECT * FROM clients WHERE id=?", (int(key),)).fetchone()
    if row is None:
        row = spine.read().execute("SELECT * FROM clients WHERE name=?", (key,)).fetchone()
    if row is None:
        row = spine.read().execute("SELECT * FROM clients WHERE name LIKE ?", (f"%{key}%",)).fetchone()
    return dict(row) if row else None


def _resolve_visible(spine, actor, key: str) -> dict:
    """Resolve + visibility in one step. An unknown OR invisible client
    yield the SAME error — a restricted worker is not told even of the
    existence of someone else's client."""
    row = _resolve_client(spine, key)
    if row is None or not client_visibility.can_see(spine, actor.user_id, row["id"], actor.role):
        raise ValueError(f"nepoznat klijent: {key!r}")
    return row


# --- readonly tools --------------------------------------------------------

def _run_search(spine, cfg, actor, args) -> dict:
    query = args["upit"]
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    hits = retrieval.search(spine, query, org_id=getattr(actor, "org_id", None),
                            visible_client_ids=visible)
    out = {"lokalno": [{"naslov": h.title, "tekst": h.text, "doc_id": h.doc_id,
                         "score": h.score} for h in hits]}
    if args.get("web") or not out["lokalno"]:
        out["web"] = websearch.ddg(query)
    return out


def _run_list_obligations(spine, cfg, actor, args) -> dict:
    kind = (args.get("vrsta") or "").strip().upper() or None
    period = args.get("period") or date.today().strftime("%Y-%m")
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    kinds = [kind] if kind else obligations.active_kinds(spine)
    out = []
    for k in kinds:
        obligations.ensure_period(spine, k, period)
        for row in obligations.list_period(spine, k, period):
            if visible is not None and row["client_id"] not in visible:
                continue
            if args.get("samo_neposlano") and row["sent"]:
                continue
            row["vrsta"] = k
            out.append(row)
    return {"period": period, "obveze": out}


def _run_client_status(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["kljuc"])
    return client_card.client_card_data(spine, cfg, row["id"])


# --- write tools -------------------------------------------------------

def _run_add_client(spine, cfg, actor, args) -> dict:
    data = {
        "name": args["naziv"],
        "oib": args.get("oib"),
        "email": args.get("email") or "",
        "phone": args.get("telefon") or "",
        "legal_form": args.get("oblik") or "",
        "regime": args.get("sustav") or "",
        "pdv_freq": args.get("pdv_ucestalost") or "monthly",
    }
    result = onboarding.create_client(spine, cfg, data, actor.username)
    # a restricted creator would otherwise not see their own work (same as POST /clients)
    if client_visibility.visible_ids(spine, actor.user_id, actor.role) is not None:
        client_visibility.grant(spine, actor.user_id, result["id"], actor.username)
    return result


def _run_edit_client(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["kljuc"])
    fields = args["polja"]
    sets, params = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        params.append(v)
    params.append(row["id"])
    with spine.write() as c:
        c.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id=?", params)
    spine.audit(actor.username, "client_update_agent", str(row["id"]), ",".join(fields))
    return {"id": row["id"], "polja": fields}


def _run_mark_obligation(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["klijent"])
    kind = args["vrsta"].strip().upper()
    period = args.get("period") or date.today().strftime("%Y-%m")
    obligations.ensure_period(spine, kind, period)
    ob = spine.read().execute(
        "SELECT id FROM obligations WHERE client_id=? AND kind=? AND period=?",
        (row["id"], kind, period),
    ).fetchone()
    if ob is None:
        raise ValueError(f"klijent {row['name']!r} nema obvezu {kind} za {period}")
    sent = bool(args.get("stanje", True))
    obligations.mark_sent(spine, ob["id"], actor.username, sent)
    return {"obligation_id": ob["id"], "client_id": row["id"], "vrsta": kind, "sent": sent}


def _run_schedule_deadline(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["klijent"])
    kind = args["vrsta"]
    due_date = args["datum"]
    item_id = expiry.add(spine, row["id"], kind, args.get("oznaka") or kind, due_date)
    spine.audit(actor.username, "expiry_add_agent", f"client:{row['id']}", kind)
    return {"id": item_id, "client_id": row["id"], "vrsta": kind, "datum": due_date}


def _run_write_note(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["klijent"])
    note_id = notes.add(spine, row["id"], actor.username, args["tekst"])
    return {"id": note_id, "client_id": row["id"]}


# --- Phase 2: promotion — more business capabilities as tools --------------

def _run_add_obligation_type(spine, cfg, actor, args) -> dict:
    kind = obligations.upsert_type(
        spine, args["kind"], args.get("label") or args["kind"], args.get("rule", ""),
        args.get("frequency", "monthly"), args.get("applies_to", "all_active"),
        active=bool(args.get("active", True)), category=args.get("category"),
        user=actor.username)
    return {"kind": kind, "label": args.get("label") or kind}


def _run_missing_documents(spine, cfg, actor, args) -> dict:
    from atlas.business import doc_completeness
    row = _resolve_visible(spine, actor, args["klijent"])
    present = sorted({r["doc_type"] for r in spine.read().execute(
        "SELECT DISTINCT doc_type FROM documents WHERE client_id=?", (row["id"],)).fetchall()})
    return {"klijent": row["name"], "prisutni": present,
            "nedostaju": doc_completeness.missing_for_client(spine, row["id"])}


def _run_query_database(spine, cfg, actor, args) -> dict:
    from atlas.rag import sql_lane
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    return {"odgovor": sql_lane.handle(spine, args["pitanje"], visible=visible)}


def _run_learn_source(spine, cfg, actor, args) -> dict:
    from atlas.web import learn
    return learn.learn_url(spine, cfg, args["url"], actor.username)


def _run_start_program(spine, cfg, actor, args) -> dict:
    from atlas.business import fleet
    res = fleet.open_on_worker(spine, args["radnik"], args["program"], actor_role=actor.role)
    if not res.get("ok"):
        raise ValueError(res["message"])
    return res


def _run_tax_deadlines(spine, cfg, actor, args) -> dict:
    from atlas.business import deadline_calendar
    days = int(args.get("dana", 14))
    return {"rokovi": [dict(r) for r in deadline_calendar.upcoming(spine, days=days)]}


def _run_client_completeness(spine, cfg, actor, args) -> dict:
    from atlas.business import checklist
    row = _resolve_visible(spine, actor, args["klijent"])
    return checklist.score_client(spine, row["id"])


def _run_send_client_message(spine, cfg, actor, args) -> dict:
    from atlas.business import messaging
    row = _resolve_visible(spine, actor, args["klijent"])
    return messaging.send_to_client(spine, cfg, row["id"], args["naslov"], args["tekst"],
                                    dry_run=False)


def _run_export_excel(spine, cfg, actor, args) -> dict:
    from atlas.business import excel_export
    what, period = args.get("sto"), args.get("period")
    questions = excel_export.clarify(what, period)
    if questions:  # info missing -> AI asks the user these questions (does not export)
        return {"pitanja": questions}
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    token, rows = excel_export.build(spine, cfg, what, period, visible)
    return {"preuzmi": f"/export/{token}", "redaka": rows, "sto": what}


def _run_load_skill(spine, cfg, actor, args) -> dict:
    """Load the full STEPS of a single skill (office procedure) on demand. Progressive
    disclosure over the existing skills registry: the agent has only the catalog
    (name+description) in the prompt up front, and pulls the full steps only when
    needed. Org-scoped."""
    from atlas.knowledge import skills as skills_mod
    name = (args.get("ime") or "").strip().lower()
    # readable: only skills THIS actor is allowed to see (private/team gate; Codex)
    active = skills_mod.readable(
        skills_mod.list_skills(spine, actor.org_id, status="active"), actor)
    match = next((s for s in active if (s["name"] or "").strip().lower() == name), None)
    if match is None:
        return {"greska": f"nepoznata vještina: {args.get('ime')!r}",
                "dostupne": [s["name"] for s in active]}
    skills_mod.mark_used(spine, match["id"])  # use_count for skill-health (dead/alive)
    return {"ime": match["name"], "koraci": match["steps"],
            "validacija": match.get("validation") or ""}


def _run_connectivity(spine, cfg, actor, args) -> dict:
    """Connectivity graph: entities from the query -> related documents -> summary.
    Read-only, client visibility is respected (hidden documents are not included). The
    tool builds the LLM client itself (the tool registry does not receive an llm);
    without an LLM it returns a list of entities."""
    from atlas.business import model_settings
    from atlas.core.llm import LLMClient
    from atlas.rag import graphrag
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    try:
        llm = model_settings.build_llm(spine, cfg)
    except Exception:
        llm = None
    return {"odgovor": graphrag.handle(spine, cfg, args["upit"], llm, visible=visible,
                                       org_id=actor.org_id)}


def _run_expiry_deadlines(spine, cfg, actor, args) -> dict:
    """Upcoming EXPIRY deadlines (ID cards, permits, certificates) — filtered by
    client visibility (a hidden client does not leak)."""
    days = int(args.get("dana", 60))
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    rows = [dict(r) for r in expiry.expiring(spine, days=days)]
    if visible is not None:  # None = sees all; otherwise filter
        rows = [r for r in rows if r["client_id"] in visible]
    return {"rokovi": rows}


def _run_client_documents(spine, cfg, actor, args) -> dict:
    """Documents in the client's file (name, type, validity) — the AI sees what the
    office has. Client visibility is checked through _resolve_visible."""
    row = _resolve_visible(spine, actor, args["klijent"])
    docs = spine.read().execute(
        "SELECT id, title, doc_type, valid_from, valid_until, stale "
        "FROM documents WHERE client_id=? ORDER BY doc_type, valid_until", (row["id"],)).fetchall()
    return {"klijent": row["name"], "dokumenti": [dict(d) for d in docs]}


def _run_propose_skill(spine, cfg, actor, args) -> dict:
    """Self-evolving skills (Abu): save a repeatable procedure as a DRAFT skill
    (owner=proposer, org-scoped, private). A draft does not enter the prompt catalog
    until the office activates it. Goes through propose->confirm like every write (the
    LLM does not create silently — the user confirms)."""
    from atlas.knowledge import skills as skills_mod
    sid = skills_mod.create_skill(
        spine, actor.org_id, args["ime"], description=args.get("opis") or "",
        trigger=args.get("okidac") or "", steps=args["koraci"],
        validation=args.get("validacija") or "", owner_user_id=actor.user_id,
        visibility="private", user=actor.username)
    return {"skill_id": sid, "ime": args["ime"], "status": "draft"}


def _run_wake_computer(spine, cfg, actor, args) -> dict:
    """Wake the worker's workstation over the network (Wake-on-LAN). Admin+, counterpart
    to the pokreni_program tool."""
    from atlas.business import fleet
    res = fleet.wake_worker(spine, args["radnik"], actor_role=actor.role)
    if not res.get("ok"):
        raise ValueError(res["message"])
    return res


# --- registry ---------------------------------------------------------

TOOLS: dict[str, Tool] = {
    "pretrazi": Tool(
        name="pretrazi",
        description="Pretraži interno znanje (RAG) i po potrebi internet.",
        schema={"type": "object",
                "properties": {"upit": {"type": "string"}, "web": {"type": "boolean"}},
                "required": ["upit"]},
        readonly=True, min_role="viewer", run=_run_search,
    ),
    "popis_obveza": Tool(
        name="popis_obveza",
        description="Popis mjesečnih obveza (PDV, JOPPD...), opcionalno filtriran.",
        schema={"type": "object",
                "properties": {"vrsta": {"type": "string"}, "period": {"type": "string"},
                               "samo_neposlano": {"type": "boolean"}},
                "required": []},
        readonly=True, min_role="viewer", run=_run_list_obligations,
    ),
    "stanje_klijenta": Tool(
        name="stanje_klijenta",
        description="Cjeloviti dosje klijenta (obveze, rokovi, bilješke, dokumenti...).",
        schema={"type": "object", "properties": {"kljuc": {"type": "string"}},
                "required": ["kljuc"]},
        readonly=True, min_role="viewer", run=_run_client_status,
    ),
    "dodaj_klijenta": Tool(
        name="dodaj_klijenta",
        description="Dodaj novog klijenta.",
        schema={"type": "object",
                "properties": {
                    "naziv": {"type": "string"}, "oib": {"type": "string"},
                    "email": {"type": "string"}, "telefon": {"type": "string"},
                    "oblik": {"type": "string"}, "sustav": {"type": "string"},
                    "pdv_ucestalost": {"type": "string"},
                },
                "required": ["naziv"]},
        readonly=False, min_role="member", run=_run_add_client,
    ),
    "uredi_klijenta": Tool(
        name="uredi_klijenta",
        description="Uredi polja postojećeg klijenta.",
        schema={"type": "object",
                "properties": {"kljuc": {"type": "string"}, "polja": {"type": "object"}},
                "required": ["kljuc", "polja"]},
        readonly=False, min_role="member", run=_run_edit_client,
    ),
    "oznaci_obvezu": Tool(
        name="oznaci_obvezu",
        description="Označi mjesečnu obvezu kao poslanu/nije poslanu.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "vrsta": {"type": "string"},
                               "stanje": {"type": "boolean"}, "period": {"type": "string"}},
                "required": ["klijent", "vrsta"]},
        readonly=False, min_role="member", run=_run_mark_obligation,
    ),
    "zakazi_rok": Tool(
        name="zakazi_rok",
        description="Zakaži rok isteka (osobna, dozvola, certifikat...) za klijenta.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "vrsta": {"type": "string"},
                               "datum": {"type": "string"}, "oznaka": {"type": "string"}},
                "required": ["klijent", "vrsta", "datum"]},
        readonly=False, min_role="member", run=_run_schedule_deadline,
    ),
    "zapisi_belesku": Tool(
        name="zapisi_belesku",
        description="Zapiši bilješku uz klijenta.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "tekst": {"type": "string"}},
                "required": ["klijent", "tekst"]},
        readonly=False, min_role="member", run=_run_write_note,
    ),
    "nedostajuci_dokumenti": Tool(
        name="nedostajuci_dokumenti",
        description="Koji obavezni dokumenti nedostaju klijentu (iz popisa obaveznih vrsta).",
        schema={"type": "object", "properties": {"klijent": {"type": "string"}},
                "required": ["klijent"]},
        readonly=True, min_role="viewer", run=_run_missing_documents,
    ),
    "upit_baze": Tool(
        name="upit_baze",
        description="Brojčani upit nad bazom (koliko klijenata/računa, zbroj, top...).",
        schema={"type": "object", "properties": {"pitanje": {"type": "string"}},
                "required": ["pitanje"]},
        readonly=True, min_role="viewer", run=_run_query_database,
    ),
    "dodaj_vrstu_obveze": Tool(
        name="dodaj_vrstu_obveze",
        description="Dodaj ili uredi VRSTU mjesečne obveze (npr. PDV, najam) i njen rok.",
        schema={"type": "object",
                "properties": {
                    "kind": {"type": "string"}, "label": {"type": "string"},
                    "rule": {"type": "string"}, "frequency": {"type": "string"},
                    "applies_to": {"type": "string"}, "category": {"type": "string"},
                    "active": {"type": "boolean"}},
                "required": ["kind"]},
        readonly=False, min_role="member", run=_run_add_obligation_type,
    ),
    "nauci_izvor": Tool(
        name="nauci_izvor",
        description="Nauči s web-stranice (URL): spremi sadržaj i prepoznate podatke u znanje.",
        schema={"type": "object", "properties": {"url": {"type": "string"}},
                "required": ["url"]},
        readonly=False, min_role="member", run=_run_learn_source,
    ),
    "pokreni_program": Tool(
        name="pokreni_program",
        description="Pokreni odobreni program na radnikovoj radnoj stanici (kod <ime> otvori <program>).",
        schema={"type": "object",
                "properties": {"radnik": {"type": "string"}, "program": {"type": "string"}},
                "required": ["radnik", "program"]},
        readonly=False, min_role="admin", run=_run_start_program,
    ),
    "porezni_rokovi": Tool(
        name="porezni_rokovi",
        description="Nadolazeći porezni rokovi (PDV, JOPPD...) u sljedećih N dana.",
        schema={"type": "object", "properties": {"dana": {"type": "integer"}}, "required": []},
        readonly=True, min_role="viewer", run=_run_tax_deadlines,
    ),
    "kompletnost_klijenta": Tool(
        name="kompletnost_klijenta",
        description="Koliko je dosje klijenta kompletan (koji podaci/dokumenti fale).",
        schema={"type": "object", "properties": {"klijent": {"type": "string"}},
                "required": ["klijent"]},
        readonly=True, min_role="viewer", run=_run_client_completeness,
    ),
    "posalji_poruku_klijentu": Tool(
        name="posalji_poruku_klijentu",
        description="Pošalji poruku (mail/kanal) klijentu — samo uz njegov pristanak.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "naslov": {"type": "string"},
                               "tekst": {"type": "string"}},
                "required": ["klijent", "naslov", "tekst"]},
        readonly=False, min_role="member", run=_run_send_client_message,
    ),
    "izvezi_excel": Tool(
        name="izvezi_excel",
        description="Izvezi podatke u Excel (klijenti ili obveze za mjesec). Ako "
                    "nedostaje info, vrati pitanja da doznaš što i kako izvesti.",
        schema={"type": "object",
                "properties": {"sto": {"type": "string"}, "period": {"type": "string"}},
                "required": []},
        readonly=True, min_role="viewer", run=_run_export_excel,
    ),
    "rokovi_isteka": Tool(
        name="rokovi_isteka",
        description="Nadolazeći rokovi isteka (osobne iskaznice, dozvole, certifikati) u N dana.",
        schema={"type": "object", "properties": {"dana": {"type": "integer"}}, "required": []},
        readonly=True, min_role="viewer", run=_run_expiry_deadlines,
    ),
    "dokumenti_klijenta": Tool(
        name="dokumenti_klijenta",
        description="Popis dokumenata koje ured ima u dosjeu klijenta (naziv, vrsta, valjanost).",
        schema={"type": "object", "properties": {"klijent": {"type": "string"}},
                "required": ["klijent"]},
        readonly=True, min_role="viewer", run=_run_client_documents,
    ),
    "probudi_racunalo": Tool(
        name="probudi_racunalo",
        description="Probudi radnikovu radnu stanicu preko mreže (Wake-on-LAN).",
        schema={"type": "object", "properties": {"radnik": {"type": "string"}},
                "required": ["radnik"]},
        readonly=False, min_role="admin", run=_run_wake_computer,
    ),
    "povezanost": Tool(
        name="povezanost",
        description="Poveznice kroz graf: kako su klijenti/dokumenti/entiteti povezani.",
        schema={"type": "object", "properties": {"upit": {"type": "string"}},
                "required": ["upit"]},
        readonly=True, min_role="viewer", run=_run_connectivity,
    ),
    "ucitaj_vjestinu": Tool(
        name="ucitaj_vjestinu",
        description="Učitaj pune upute imenovane vještine (procedure ureda) kad je relevantna.",
        schema={"type": "object", "properties": {"ime": {"type": "string"}},
                "required": ["ime"]},
        readonly=True, min_role="viewer", run=_run_load_skill,
    ),
    "predlozi_vjestinu": Tool(
        name="predlozi_vjestinu",
        description="Predloži spremanje upravo odrađene ponovljive procedure kao "
                    "NOVE vještine (nacrt) — ponudi kad korisnik radnju vjerojatno ponavlja.",
        schema={"type": "object",
                "properties": {"ime": {"type": "string"}, "opis": {"type": "string"},
                               "okidac": {"type": "string"}, "koraci": {"type": "string"},
                               "validacija": {"type": "string"}},
                "required": ["ime", "koraci"]},
        readonly=False, min_role="member", run=_run_propose_skill,
    ),
}

_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
_TYPE_MAP = {"string": str, "boolean": bool, "object": dict, "number": (int, float), "integer": int}


def validate(name: str, args: dict) -> tuple[bool, str | None]:
    """Pure validation (no spine): schema (required fields, types) + domain
    (OIB format, ISO date, allowed fields for uredi_klijenta). Client existence
    is checked in run_tool (needs spine)."""
    tool = TOOLS.get(name)
    if tool is None:
        return False, f"nepoznat alat: {name!r}"
    if not isinstance(args, dict):
        return False, "argumenti moraju biti objekt"

    schema = tool.schema
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if args.get(req) in (None, ""):
            return False, f"nedostaje obavezno polje: {req!r}"
    for k, v in args.items():
        spec = props.get(k)
        if spec is None:
            continue
        expected = _TYPE_MAP.get(spec.get("type"))
        # bool is a subclass of int in Python — do not let the "number"/"integer"
        # check silently see through True/False.
        if expected and (not isinstance(v, expected) or
                         (expected is not bool and isinstance(v, bool))):
            return False, f"polje {k!r} mora biti tipa {spec['type']}"

    if args.get("oib") and not security.oib_valid(args["oib"]):
        return False, "neispravan OIB"
    if args.get("datum"):
        try:
            date.fromisoformat(args["datum"])
        except ValueError:
            return False, "datum mora biti ISO format (GGGG-MM-DD)"
    if name == "uredi_klijenta":
        fields = args.get("polja") or {}
        bad = set(fields) - _EDITABLE_FIELDS
        if bad:
            return False, f"nedozvoljena polja: {sorted(bad)}"

    return True, None


# External side effect outside the office (message to a client, starting/waking a
# workstation, fetching from the web) — the highest tier. Other writes write to the
# office database (med), readonly ones only read (low). OpenWorker "tier consequential
# actions" pattern.
_HIGH_RISK = frozenset({"posalji_poruku_klijentu", "pokreni_program",
                        "probudi_racunalo", "nauci_izvor"})


def risk(name: str) -> str:
    """Tool risk: 'low' (reads), 'med' (writes to the office database), 'high'
    (external side effect). Unknown tool -> 'high' (fail-safe: unclassified = most
    cautious tier). Pure function — the UI/confirmation shows the severity, high
    requires a stronger confirmation.

    ADVISORY (Codex): this is a LABEL for display, not a gate — write tools go through
    propose->confirm anyway. Some readonly tools have incidental effects this label
    does NOT capture: `pretrazi` (web=True/auto) sends the query outside the LAN,
    `popis_obveza`/`izvezi_excel` trigger ensure_period (materialization of
    obligations). These are pre-existing behaviors; egress-with-confirmation is a
    separate broader change."""
    tool = TOOLS.get(name)
    if tool is None or name in _HIGH_RISK:
        return "high"
    return "low" if tool.readonly else "med"


def allowed(actor, tool) -> bool:
    """min_role gate. `tool` can be a Tool or a tool name."""
    if isinstance(tool, str):
        tool = TOOLS.get(tool)
        if tool is None:
            return False
    return _ROLE_RANK.get(actor.role, -1) >= _ROLE_RANK.get(tool.min_role, 0)


def run_tool(spine, cfg, actor, name: str, args: dict) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        raise ValueError(f"nepoznat alat: {name!r}")
    if not allowed(actor, tool):
        raise ValueError(f"uloga {actor.role!r} ne smije alat {name!r}")
    ok, err = validate(name, args)
    if not ok:
        raise ValueError(err)
    return tool.run(spine, cfg, actor, args)
