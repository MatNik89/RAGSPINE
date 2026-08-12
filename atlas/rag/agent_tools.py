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


# --- Faza 2: promocija — više business sposobnosti kao alati --------------

def _run_dodaj_vrstu_obveze(spine, cfg, actor, args) -> dict:
    kind = obveze.upsert_type(
        spine, args["kind"], args.get("label") or args["kind"], args.get("rule", ""),
        args.get("frequency", "monthly"), args.get("applies_to", "all_active"),
        active=bool(args.get("active", True)), category=args.get("category"),
        user=actor.username)
    return {"kind": kind, "label": args.get("label") or kind}


def _run_nedostajuci_dokumenti(spine, cfg, actor, args) -> dict:
    from atlas.business import doc_completeness
    row = _resolve_visible(spine, actor, args["klijent"])
    present = sorted({r["doc_type"] for r in spine.read().execute(
        "SELECT DISTINCT doc_type FROM documents WHERE client_id=?", (row["id"],)).fetchall()})
    return {"klijent": row["name"], "prisutni": present,
            "nedostaju": doc_completeness.missing_for_client(spine, row["id"])}


def _run_upit_baze(spine, cfg, actor, args) -> dict:
    from atlas.rag import sql_lane
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    return {"odgovor": sql_lane.handle(spine, args["pitanje"], visible=visible)}


def _run_nauci_izvor(spine, cfg, actor, args) -> dict:
    from atlas.web import learn
    return learn.learn_url(spine, cfg, args["url"], actor.username)


def _run_pokreni_program(spine, cfg, actor, args) -> dict:
    from atlas.business import fleet
    res = fleet.open_on_worker(spine, args["radnik"], args["program"], actor_role=actor.role)
    if not res.get("ok"):
        raise ValueError(res["message"])
    return res


def _run_porezni_rokovi(spine, cfg, actor, args) -> dict:
    from atlas.business import kalendar
    dana = int(args.get("dana", 14))
    return {"rokovi": [dict(r) for r in kalendar.upcoming(spine, days=dana)]}


def _run_kompletnost_klijenta(spine, cfg, actor, args) -> dict:
    from atlas.business import checklist
    row = _resolve_visible(spine, actor, args["klijent"])
    return checklist.score_client(spine, row["id"])


def _run_posalji_poruku_klijentu(spine, cfg, actor, args) -> dict:
    from atlas.business import messaging
    row = _resolve_visible(spine, actor, args["klijent"])
    return messaging.send_to_client(spine, cfg, row["id"], args["naslov"], args["tekst"],
                                    dry_run=False)


def _run_izvezi_excel(spine, cfg, actor, args) -> dict:
    from atlas.business import excel_export
    sto, period = args.get("sto"), args.get("period")
    pitanja = excel_export.clarify(sto, period)
    if pitanja:  # nedostaje info -> AI postavi ova pitanja korisniku (ne izvozi)
        return {"pitanja": pitanja}
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    token, rows = excel_export.build(spine, cfg, sto, period, visible)
    return {"preuzmi": f"/export/{token}", "redaka": rows, "sto": sto}


def _run_ucitaj_vjestinu(spine, cfg, actor, args) -> dict:
    """Učitaj pune KORAKE jedne vještine (procedure ureda) na zahtjev. Progresivno
    otkrivanje nad postojećim skills-registrom: agentu je unaprijed u promptu samo
    katalog (ime+opis), pune korake povlači tek kad zatreba. Org-scoped."""
    from atlas.knowledge import skills as skills_mod
    ime = (args.get("ime") or "").strip().lower()
    # readable: samo vještine koje OVAJ actor smije vidjeti (private/team gate; Codex)
    aktivne = skills_mod.readable(
        skills_mod.list_skills(spine, actor.org_id, status="active"), actor)
    match = next((s for s in aktivne if (s["name"] or "").strip().lower() == ime), None)
    if match is None:
        return {"greska": f"nepoznata vještina: {args.get('ime')!r}",
                "dostupne": [s["name"] for s in aktivne]}
    skills_mod.mark_used(spine, match["id"])  # use_count za skill-health (mrtve/žive)
    return {"ime": match["name"], "koraci": match["steps"],
            "validacija": match.get("validation") or ""}


def _run_povezanost(spine, cfg, actor, args) -> dict:
    """Graf povezanosti: entiteti iz upita -> povezani dokumenti -> sažetak. Read-only,
    vidljivost klijenata se poštuje (skriveni dokumenti se ne uključuju). Alat sam
    gradi LLM klijenta (registar alata ne prima llm); bez LLM-a vrati listu entiteta."""
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


def _run_rokovi_isteka(spine, cfg, actor, args) -> dict:
    """Nadolazeći rokovi ISTEKA (osobne, dozvole, certifikati) — filtrirano po
    vidljivosti klijenata (skriveni klijent ne curi)."""
    dana = int(args.get("dana", 60))
    visible = client_visibility.visible_ids(spine, actor.user_id, actor.role)
    rows = [dict(r) for r in expiry.expiring(spine, days=dana)]
    if visible is not None:  # None = vidi sve; inače filtriraj
        rows = [r for r in rows if r["client_id"] in visible]
    return {"rokovi": rows}


def _run_dokumenti_klijenta(spine, cfg, actor, args) -> dict:
    """Dokumenti u dosjeu klijenta (naziv, vrsta, valjanost) — AI vidi što ured
    ima. Vidljivost klijenta se provjerava kroz _resolve_visible."""
    row = _resolve_visible(spine, actor, args["klijent"])
    docs = spine.read().execute(
        "SELECT id, title, doc_type, valid_from, valid_until, stale "
        "FROM documents WHERE client_id=? ORDER BY doc_type, valid_until", (row["id"],)).fetchall()
    return {"klijent": row["name"], "dokumenti": [dict(d) for d in docs]}


def _run_predlozi_vjestinu(spine, cfg, actor, args) -> dict:
    """Self-evolving skills (Abu): spremi ponovljivu proceduru kao DRAFT vještinu
    (owner=predlagatelj, org-scoped, private). Draft ne ulazi u prompt-katalog dok
    ga ured ne aktivira. Ide kroz propose->confirm kao svaki write (LLM ne kreira
    tiho — korisnik potvrdi)."""
    from atlas.knowledge import skills as skills_mod
    sid = skills_mod.create_skill(
        spine, actor.org_id, args["ime"], description=args.get("opis") or "",
        trigger=args.get("okidac") or "", steps=args["koraci"],
        validation=args.get("validacija") or "", owner_user_id=actor.user_id,
        visibility="private", user=actor.username)
    return {"skill_id": sid, "ime": args["ime"], "status": "draft"}


def _run_probudi_racunalo(spine, cfg, actor, args) -> dict:
    """Probudi radnikovu radnu stanicu preko mreže (Wake-on-LAN). Admin+, pandan
    alatu pokreni_program."""
    from atlas.business import fleet
    res = fleet.wake_worker(spine, args["radnik"], actor_role=actor.role)
    if not res.get("ok"):
        raise ValueError(res["message"])
    return res


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
    "nedostajuci_dokumenti": Tool(
        name="nedostajuci_dokumenti",
        description="Koji obavezni dokumenti nedostaju klijentu (iz popisa obaveznih vrsta).",
        schema={"type": "object", "properties": {"klijent": {"type": "string"}},
                "required": ["klijent"]},
        readonly=True, min_role="viewer", run=_run_nedostajuci_dokumenti,
    ),
    "upit_baze": Tool(
        name="upit_baze",
        description="Brojčani upit nad bazom (koliko klijenata/računa, zbroj, top...).",
        schema={"type": "object", "properties": {"pitanje": {"type": "string"}},
                "required": ["pitanje"]},
        readonly=True, min_role="viewer", run=_run_upit_baze,
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
        readonly=False, min_role="member", run=_run_dodaj_vrstu_obveze,
    ),
    "nauci_izvor": Tool(
        name="nauci_izvor",
        description="Nauči s web-stranice (URL): spremi sadržaj i prepoznate podatke u znanje.",
        schema={"type": "object", "properties": {"url": {"type": "string"}},
                "required": ["url"]},
        readonly=False, min_role="member", run=_run_nauci_izvor,
    ),
    "pokreni_program": Tool(
        name="pokreni_program",
        description="Pokreni odobreni program na radnikovoj radnoj stanici (kod <ime> otvori <program>).",
        schema={"type": "object",
                "properties": {"radnik": {"type": "string"}, "program": {"type": "string"}},
                "required": ["radnik", "program"]},
        readonly=False, min_role="admin", run=_run_pokreni_program,
    ),
    "porezni_rokovi": Tool(
        name="porezni_rokovi",
        description="Nadolazeći porezni rokovi (PDV, JOPPD...) u sljedećih N dana.",
        schema={"type": "object", "properties": {"dana": {"type": "integer"}}, "required": []},
        readonly=True, min_role="viewer", run=_run_porezni_rokovi,
    ),
    "kompletnost_klijenta": Tool(
        name="kompletnost_klijenta",
        description="Koliko je dosje klijenta kompletan (koji podaci/dokumenti fale).",
        schema={"type": "object", "properties": {"klijent": {"type": "string"}},
                "required": ["klijent"]},
        readonly=True, min_role="viewer", run=_run_kompletnost_klijenta,
    ),
    "posalji_poruku_klijentu": Tool(
        name="posalji_poruku_klijentu",
        description="Pošalji poruku (mail/kanal) klijentu — samo uz njegov pristanak.",
        schema={"type": "object",
                "properties": {"klijent": {"type": "string"}, "naslov": {"type": "string"},
                               "tekst": {"type": "string"}},
                "required": ["klijent", "naslov", "tekst"]},
        readonly=False, min_role="member", run=_run_posalji_poruku_klijentu,
    ),
    "izvezi_excel": Tool(
        name="izvezi_excel",
        description="Izvezi podatke u Excel (klijenti ili obveze za mjesec). Ako "
                    "nedostaje info, vrati pitanja da doznaš što i kako izvesti.",
        schema={"type": "object",
                "properties": {"sto": {"type": "string"}, "period": {"type": "string"}},
                "required": []},
        readonly=True, min_role="viewer", run=_run_izvezi_excel,
    ),
    "rokovi_isteka": Tool(
        name="rokovi_isteka",
        description="Nadolazeći rokovi isteka (osobne iskaznice, dozvole, certifikati) u N dana.",
        schema={"type": "object", "properties": {"dana": {"type": "integer"}}, "required": []},
        readonly=True, min_role="viewer", run=_run_rokovi_isteka,
    ),
    "dokumenti_klijenta": Tool(
        name="dokumenti_klijenta",
        description="Popis dokumenata koje ured ima u dosjeu klijenta (naziv, vrsta, valjanost).",
        schema={"type": "object", "properties": {"klijent": {"type": "string"}},
                "required": ["klijent"]},
        readonly=True, min_role="viewer", run=_run_dokumenti_klijenta,
    ),
    "probudi_racunalo": Tool(
        name="probudi_racunalo",
        description="Probudi radnikovu radnu stanicu preko mreže (Wake-on-LAN).",
        schema={"type": "object", "properties": {"radnik": {"type": "string"}},
                "required": ["radnik"]},
        readonly=False, min_role="admin", run=_run_probudi_racunalo,
    ),
    "povezanost": Tool(
        name="povezanost",
        description="Poveznice kroz graf: kako su klijenti/dokumenti/entiteti povezani.",
        schema={"type": "object", "properties": {"upit": {"type": "string"}},
                "required": ["upit"]},
        readonly=True, min_role="viewer", run=_run_povezanost,
    ),
    "ucitaj_vjestinu": Tool(
        name="ucitaj_vjestinu",
        description="Učitaj pune upute imenovane vještine (procedure ureda) kad je relevantna.",
        schema={"type": "object", "properties": {"ime": {"type": "string"}},
                "required": ["ime"]},
        readonly=True, min_role="viewer", run=_run_ucitaj_vjestinu,
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
        readonly=False, min_role="member", run=_run_predlozi_vjestinu,
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


# Vanjska nuspojava izvan ureda (poruka klijentu, pokretanje/buđenje radne
# stanice, dohvat s weba) — najviši tier. Ostali write pišu u bazu ureda (med),
# readonly samo čitaju (low). OpenWorker "tier consequential actions" obrazac.
_HIGH_RISK = frozenset({"posalji_poruku_klijentu", "pokreni_program",
                        "probudi_racunalo", "nauci_izvor"})


def risk(name: str) -> str:
    """Rizik alata: 'low' (čita), 'med' (piše u bazu ureda), 'high' (vanjska
    nuspojava). Nepoznat alat -> 'high' (fail-safe: neklasificirano = najoprezniji
    tier). Čista funkcija — UI/potvrda prikažu jačinu, high traži jaču potvrdu.

    SAVJETODAVNO (Codex): ovo je LABEL za prikaz, ne gate — write alati ionako idu
    kroz propose->confirm. Neki readonly alati imaju uzgredne efekte koje ovaj
    label NE hvata: `pretrazi` (web=True/auto) šalje upit van LAN-a, `popis_obveza`/
    `izvezi_excel` okidaju ensure_period (materijalizacija obveza). To su
    pre-postojeća ponašanja; egress-uz-potvrdu je zaseban širi zahvat."""
    tool = TOOLS.get(name)
    if tool is None or name in _HIGH_RISK:
        return "high"
    return "low" if tool.readonly else "med"


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
