"""Perzistentni approval-grantovi: "potvrdi jednom, zapamti" za ponovljivu radnju
(MateClaw ApprovalGrant + AutoGrantSafetyFloor, prilagođeno ATLAS-u).

SIGURNOSNI POD (tvrdo, nepromjenjivo): alat VISOKOG rizika (vanjska nuspojava —
poruka klijentu, pokretanje/buđenje stanice, dohvat s weba) NIKAD ne prolazi
automatski, čak i uz postojeći grant. Auto-odobrenje moguće SAMO za low/med.

Grant veže: scope (user|org) + TOČAN alat + TOČAN cilj (klijent/obveza) + max_risk
(low|med) + rok. Exact-target: bez jasnog cilj-arga koristi se cijeli skup
argumenata (nikad "bilo koji cilj za ovaj alat")."""
import json

from atlas.business.acl import ROLE_RANK

_RANK = {"low": 0, "med": 1, "high": 2}

# cilj-ključevi po alatu (identitet radnje); ako ih nema -> cijeli args (exact).
# NAPOMENA: oznaci_obvezu/zakazi_rok NAMJERNO ne uključuju period/datum — grant je
# "za ovog klijenta+vrstu kroz razdoblja" (rekurencija je smisao "zapamti"); radnje
# su reverzibilne+interne i safety-floor jamči da nikad ne okinu vanjski efekt.
_TARGET_KEYS = {
    "oznaci_obvezu": ("klijent", "vrsta"),
    "zakazi_rok": ("klijent", "vrsta"),
    "zapisi_belesku": ("klijent",),
    "uredi_klijenta": ("kljuc",),
    "dodaj_klijenta": ("naziv",),
    "dodaj_vrstu_obveze": ("kind",),
    "izvezi_excel": ("sto",),
    "predlozi_vjestinu": ("ime",),
}


def target_for(name: str, args: dict) -> str:
    """Kanonski JSON cilja (bez '|' kolizije; Codex). Za alate s cilj-ključevima
    uzmi taj podskup, inače cijeli args (exact-target)."""
    keys = _TARGET_KEYS.get(name)
    src = {k: (args or {}).get(k, "") for k in keys} if keys else (args or {})
    return json.dumps(src, sort_keys=True, ensure_ascii=False, default=str)


def _target_empty(name: str, args: dict) -> bool:
    """True ako cilj nema nijednu značajnu vrijednost (npr. dodaj_klijenta bez
    naziva) -> grant bi bio wildcard; to se ODBIJA (Codex)."""
    keys = _TARGET_KEYS.get(name)
    if not keys:
        return not (args or {})  # bez cilj-ključeva: prazan args = wildcard
    return not any(str((args or {}).get(k, "")).strip() for k in keys)


def can_auto_approve(spine, actor, name: str, args: dict) -> bool:
    """Smije li se `name(args)` automatski izvršiti bez potvrde? SAMO ako: rizik
    nije high (safety-floor) I postoji važeći grant (scope/alat/cilj/rizik)."""
    from atlas.rag import agent_tools
    risk = agent_tools.risk(name)
    if _RANK.get(risk, 2) >= _RANK["high"]:
        return False  # SAFETY FLOOR: high nikad auto
    target = target_for(name, args)
    row = spine.read().execute(
        """SELECT 1 FROM agent_grants
           WHERE revoked=0 AND tool=? AND org_id=?
             AND (scope='org' OR (scope='user' AND user_id=?))
             AND (target='' OR target=?)
             AND (expire_at IS NULL OR expire_at > datetime('now'))
             AND CASE max_risk WHEN 'low' THEN 0 WHEN 'med' THEN 1 ELSE 2 END >= ?
           LIMIT 1""",
        (name, actor.org_id, actor.user_id, target, _RANK.get(risk, 2))).fetchone()
    return row is not None


def create_grant(spine, actor, name: str, args: dict, scope: str = "user",
                 days: int | None = None, user: str = "?") -> int:
    """Stvori grant za (alat, cilj). max_risk = rizik alata; ODBIJ ako je high
    (safety-floor: high se ne može ni zapamtiti). org-scope traži owner/admin
    (provjerava se u ruti). `days` None = trajno."""
    from atlas.rag import agent_tools
    if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["member"]:
        raise ValueError("za pravilo odobrenja potrebna je barem member uloga")  # Codex
    if name not in agent_tools.TOOLS:
        raise ValueError(f"nepoznat alat: {name!r}")
    risk = agent_tools.risk(name)
    if _RANK.get(risk, 2) >= _RANK["high"]:
        raise ValueError("radnja visokog rizika ne može se automatski odobriti (uvijek traži potvrdu)")
    if scope not in ("user", "org"):
        raise ValueError("scope mora biti 'user' ili 'org'")
    # grant nosi SAMO cilj (klijent/obveza), ne pun poziv -> ne validiramo sve
    # obavezne args-e; dovoljno je da cilj NIJE prazan (inače wildcard; Codex)
    if _target_empty(name, args):
        raise ValueError("pravilo mora imati konkretan cilj (nije dopušten wildcard)")
    target = target_for(name, args)
    expire = None if not days else f"datetime('now', '+{int(days)} days')"
    with spine.write() as c:
        exp_val = c.execute(f"SELECT {expire} AS e").fetchone()["e"] if expire else None
        gid = c.execute(
            "INSERT INTO agent_grants(org_id,scope,user_id,tool,target,max_risk,expire_at,created_by) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (actor.org_id, scope, actor.user_id, name, target, risk, exp_val, user)).lastrowid
    spine.audit(user, "grant_create", f"{name}:{scope}", target[:100])
    return gid


def list_grants(spine, actor) -> list[dict]:
    """Grantovi koji vrijede za ovog actora (svoji user + org), neopozvani."""
    rows = spine.read().execute(
        "SELECT id, scope, tool, target, max_risk, expire_at, created_by, created_at "
        "FROM agent_grants WHERE revoked=0 AND org_id=? "
        "AND (scope='org' OR (scope='user' AND user_id=?)) ORDER BY id DESC",
        (actor.org_id, actor.user_id)).fetchall()
    # org-cilj može sadržavati ime/OIB klijenta kojeg restringirani radnik ne smije
    # vidjeti -> maskiraj ne-adminu (Codex; vlastiti user-grantovi ostaju vidljivi)
    is_admin = ROLE_RANK.get(actor.role, 0) >= ROLE_RANK["admin"]
    out = []
    for r in rows:
        d = dict(r)
        if d["scope"] == "org" and not is_admin:
            d["target"] = "…"
        out.append(d)
    return out


def revoke_grant(spine, actor, grant_id: int, is_owner: bool, user: str = "?") -> bool:
    """Opozovi grant. Vlastiti user-grant smije vlasnik granta; org-grant samo owner."""
    row = spine.read().execute(
        "SELECT scope, user_id FROM agent_grants WHERE id=? AND org_id=? AND revoked=0",
        (grant_id, actor.org_id)).fetchone()
    if row is None:
        return False
    if row["scope"] == "org" and not is_owner:
        raise ValueError("org-grant opoziva samo vlasnik")
    if row["scope"] == "user" and row["user_id"] != actor.user_id and not is_owner:
        raise ValueError("tuđi grant ne možete opozvati")
    with spine.write() as c:
        c.execute("UPDATE agent_grants SET revoked=1 WHERE id=? AND org_id=?", (grant_id, actor.org_id))
    spine.audit(user, "grant_revoke", f"grant:{grant_id}")
    return True
