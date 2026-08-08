# Registar alata za agentski chat sloj (Faza 3). Svaki alat zove POSTOJEĆI
# business sloj — ovaj modul samo: opisuje shemu za LLM, validira argumente,
# provjeri ovlasti (rola + vidljivost klijenta) i prosljeđuje poziv.
#
# LLM izlaz je NEPOVJERLJIV: alat popis je fiksan (allowlist), argumenti se
# validiraju kodom PRIJE svakog poziva, nepoznat/nedopušten alat -> ValueError.
#
# ponytail: pretrazi() ne prima llm (run_tool ga ne prosljeđuje) pa ne zove
# pipeline.answer (koji bez llm-a na chat lane vraća "LLM nedostupan") —
# vraća sirove pogotke (retrieval.search + websearch.ddg) koje agentska
# petlja (T3) daje LLM-u da sroči odgovor. Upgrade path: proslijedi llm kroz
# run_tool ako se pretrazi ikad treba sam sročiti sažetak.
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

from atlas.business import client_visibility, expiry, karton, notes, obveze, onboarding
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


# --- pomoćno: klijent po ključu (oib > id > naziv) ------------------------

def _resolve_client(spine, kljuc: str) -> dict | None:
    kljuc = (kljuc or "").strip()
    if not kljuc:
        return None
    row = None
    if re.fullmatch(r"\d{11}", kljuc):
        row = spine.read().execute("SELECT * FROM clients WHERE oib=?", (kljuc,)).fetchone()
    if row is None and kljuc.isdigit():
        row = spine.read().execute("SELECT * FROM clients WHERE id=?", (int(kljuc),)).fetchone()
    if row is None:
        row = spine.read().execute("SELECT * FROM clients WHERE name=?", (kljuc,)).fetchone()
    if row is None:
        row = spine.read().execute("SELECT * FROM clients WHERE name LIKE ?", (f"%{kljuc}%",)).fetchone()
    return dict(row) if row else None


def _resolve_visible(spine, actor, kljuc: str) -> dict:
    """Resolve + vidljivost u jednom koraku. Nepoznat ILI nevidljiv klijent
    daju ISTU grešku — restringiranom radniku se ne otkriva ni postojanje
    tuđeg klijenta."""
    row = _resolve_client(spine, kljuc)
    if row is None or not client_visibility.can_see(spine, actor.user_id, row["id"], actor.role):
        raise ValueError(f"nepoznat klijent: {kljuc!r}")
    return row


# --- readonly alati --------------------------------------------------------

def _run_pretrazi(spine, cfg, actor, args) -> dict:
    upit = args["upit"]
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    hits = retrieval.search(spine, upit, org_id=getattr(actor, "org_id", None),
                            visible_client_ids=visible)
    out = {"lokalno": [{"naslov": h.title, "tekst": h.text, "doc_id": h.doc_id,
                         "score": h.score} for h in hits]}
    if args.get("web") or not out["lokalno"]:
        out["web"] = websearch.ddg(upit)
    return out


def _run_popis_obveza(spine, cfg, actor, args) -> dict:
    vrsta = (args.get("vrsta") or "").strip().upper() or None
    period = args.get("period") or date.today().strftime("%Y-%m")
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    kinds = [vrsta] if vrsta else obveze.active_kinds(spine)
    out = []
    for k in kinds:
        obveze.ensure_period(spine, k, period)
        for row in obveze.list_period(spine, k, period):
            if visible is not None and row["client_id"] not in visible:
                continue
            if args.get("samo_neposlano") and row["sent"]:
                continue
            row["vrsta"] = k
            out.append(row)
    return {"period": period, "obveze": out}


def _run_stanje_klijenta(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["kljuc"])
    return karton.karton_data(spine, cfg, row["id"])


# --- write alati -------------------------------------------------------

def _run_dodaj_klijenta(spine, cfg, actor, args) -> dict:
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
    # restringirani kreator inače ne bi vidio vlastito djelo (isto kao POST /clients)
    if client_visibility.visible_ids(spine, actor.user_id, actor.role) is not None:
        client_visibility.grant(spine, actor.user_id, result["id"], actor.username)
    return result


def _run_uredi_klijenta(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["kljuc"])
    polja = args["polja"]
    sets, params = [], []
    for k, v in polja.items():
        sets.append(f"{k}=?")
        params.append(v)
    params.append(row["id"])
    with spine.write() as c:
        c.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id=?", params)
    spine.audit(actor.username, "client_update_agent", str(row["id"]), ",".join(polja))
    return {"id": row["id"], "polja": polja}


def _run_oznaci_obvezu(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["klijent"])
    vrsta = args["vrsta"].strip().upper()
    period = args.get("period") or date.today().strftime("%Y-%m")
    obveze.ensure_period(spine, vrsta, period)
    ob = spine.read().execute(
        "SELECT id FROM obligations WHERE client_id=? AND kind=? AND period=?",
        (row["id"], vrsta, period),
    ).fetchone()
    if ob is None:
        raise ValueError(f"klijent {row['name']!r} nema obvezu {vrsta} za {period}")
    sent = bool(args.get("stanje", True))
    obveze.mark_sent(spine, ob["id"], actor.username, sent)
    return {"obligation_id": ob["id"], "client_id": row["id"], "vrsta": vrsta, "sent": sent}


def _run_zakazi_rok(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["klijent"])
    vrsta = args["vrsta"]
    datum = args["datum"]
    item_id = expiry.add(spine, row["id"], vrsta, args.get("oznaka") or vrsta, datum)
    spine.audit(actor.username, "expiry_add_agent", f"client:{row['id']}", vrsta)
    return {"id": item_id, "client_id": row["id"], "vrsta": vrsta, "datum": datum}


def _run_zapisi_belesku(spine, cfg, actor, args) -> dict:
    row = _resolve_visible(spine, actor, args["klijent"])
    note_id = notes.add(spine, row["id"], actor.username, args["tekst"])
    return {"id": note_id, "client_id": row["id"]}


# --- registar ---------------------------------------------------------

TOOLS: dict[str, Tool] = {
    "pretrazi": Tool(
        name="pretrazi",
        description="Pretraži interno znanje (RAG) i po potrebi internet.",
        schema={"type": "object",
                "properties": {"upit": {"type": "string"}, "web": {"type": "boolean"}},
                "required": ["upit"]},
        readonly=True, min_role="viewer", run=_run_pretrazi,
    ),
    "popis_obveza": Tool(
        name="popis_obveza",
        description="Popis mjesečnih obveza (PDV, JOPPD...), opcionalno filtriran.",
        schema={"type": "object",
                "properties": {"vrsta": {"type": "string"}, "period": {"type": "string"},
                               "samo_neposlano": {"type": "boolean"}},
                "required": []},
        readonly=True, min_role="viewer", run=_run_popis_obveza,
    ),
    "stanje_klijenta": Tool(
        name="stanje_klijenta",
        description="Cjeloviti dosje klijenta (obveze, rokovi, bilješke, dokumenti...).",
        schema={"type": "object", "properties": {"kljuc": {"type": "string"}},
                "required": ["kljuc"]},
        readonly=True, min_role="viewer", run=_run_stanje_klijenta,
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
        readonly=False, min_role="member", run=_run_dodaj_klijenta,
    ),
    "uredi_klijenta": Tool(
        name="uredi_klijenta",
        description="Uredi polja postojećeg klijenta.",
        schema={"type": "object",
                "properties": {"kljuc": {"type": "string"}, "polja": {"type": "object"}},
                "required": ["kljuc", "polja"]},
        readonly=False, min_role="member", run=_run_uredi_klijenta,
    ),
    "oznaci_obvezu": Tool(
        name="oznaci_obvezu",
        description="Označi mjesečnu obvezu kao poslanu/nije poslanu.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "vrsta": {"type": "string"},
                               "stanje": {"type": "boolean"}, "period": {"type": "string"}},
                "required": ["klijent", "vrsta"]},
        readonly=False, min_role="member", run=_run_oznaci_obvezu,
    ),
    "zakazi_rok": Tool(
        name="zakazi_rok",
        description="Zakaži rok isteka (osobna, dozvola, certifikat...) za klijenta.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "vrsta": {"type": "string"},
                               "datum": {"type": "string"}, "oznaka": {"type": "string"}},
                "required": ["klijent", "vrsta", "datum"]},
        readonly=False, min_role="member", run=_run_zakazi_rok,
    ),
    "zapisi_belesku": Tool(
        name="zapisi_belesku",
        description="Zapiši bilješku uz klijenta.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "tekst": {"type": "string"}},
                "required": ["klijent", "tekst"]},
        readonly=False, min_role="member", run=_run_zapisi_belesku,
    ),
}

_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
_TYPE_MAP = {"string": str, "boolean": bool, "object": dict, "number": (int, float), "integer": int}


def validate(name: str, args: dict) -> tuple[bool, str | None]:
    """Čista provjera (bez spine): shema (obavezna polja, tipovi) + domenska
    (OIB format, ISO datum, dozvoljena polja za uredi_klijenta). Postojanje
    klijenta se provjerava u run_tool (treba spine)."""
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
        # bool je podklasa int-a u Pythonu — ne dopusti da "number"/"integer"
        # provjera tiho progleda kroz True/False.
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
        polja = args.get("polja") or {}
        bad = set(polja) - _EDITABLE_FIELDS
        if bad:
            return False, f"nedozvoljena polja: {sorted(bad)}"

    return True, None


def allowed(actor, tool) -> bool:
    """min_role gate. `tool` može biti Tool ili ime alata."""
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
