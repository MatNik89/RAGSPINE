# Mjesečne obveze po klijentu — data-driven registar vrsta (obligation_types).
#
# Vrste obveza (PDV, JOPPD, najam, ...) žive u tablici obligation_types, ne u
# kodu. Radnik ih dodaje/uređuje kroz UI; ovaj modul samo čita registar i za
# svaku vrstu razriješi TKO je obveznik (applies_to) pa napravi obligations rede.

# Ugrađene default-vrste — seed-aju se lijeno (INSERT OR IGNORE) pa admin-izmjene
# ostaju. (kind, label, rule, frequency, applies_to, sort, active)
# active=0 znači: postoji u registru, ali nije tab dok ga radnik ne uključi.
DEFAULT_TYPES = [
    ("PDV", "PDV", "monthly:20", "monthly", "pdv", 10, 1),
    ("JOPPD", "JOPPD", "monthly:15", "monthly", "employees", 20, 1),
    ("DOH", "Prijava poreza na dohodak (DOH)", "yearly:02-28", "yearly", "dohodak", 30, 0),
    ("PO-SD", "Paušalno izvješće (PO-SD)", "yearly:01-15", "yearly", "pausal", 40, 0),
    ("PD", "Prijava poreza na dobit (PD)", "yearly:04-30", "yearly", "dobit", 50, 0),
]

# Back-compat: neki moduli/testovi importaju obveze.KINDS. Registar je izvor
# istine; ovo su samo default-kindovi.
KINDS = tuple(t[0] for t in DEFAULT_TYPES)

# Tko je obveznik. Uz jednostavne (pdv/employees/all_active/manual) — porezni
# sustav klijenta (regime): DOH=dohodaš, PO-SD=paušalist, PD/GFI=dobitaš.
_REGIME_APPLIES = ("dobit", "dohodak", "pausal")
APPLIES_TO = ("pdv", "employees", "all_active", "manual", *_REGIME_APPLIES)
REGIMES = ("", *_REGIME_APPLIES)
FREQUENCIES = ("monthly", "quarterly", "yearly")
# Mjeseci u kojima tromjesečni obveznik predaje (nakon isteka kvartala).
_QUARTER_MONTHS = (1, 4, 7, 10)


def _yearly_month(rule: str) -> int:
    """Mjesec roka za godišnju vrstu iz pravila 'yearly:MM-DD'. Default siječanj."""
    try:
        return int((rule or "").split(":", 1)[1][:2])
    except (IndexError, ValueError):
        return 1


def _ensure_seeded(spine) -> None:
    with spine.write() as c:
        for kind, label, rule, freq, applies, sort, active in DEFAULT_TYPES:
            c.execute(
                """INSERT OR IGNORE INTO obligation_types
                   (kind, label, rule, frequency, applies_to, active, sort)
                   VALUES(?,?,?,?,?,?,?)""",
                (kind, label, rule, freq, applies, active, sort),
            )


_TYPE_COLS = "kind, label, rule, frequency, applies_to, active, sort, description"


def list_types(spine, active_only: bool = False) -> list[dict]:
    _ensure_seeded(spine)
    q = f"SELECT {_TYPE_COLS} FROM obligation_types"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY sort, kind"
    return [dict(r) for r in spine.read().execute(q).fetchall()]


def get_type(spine, kind: str) -> dict | None:
    _ensure_seeded(spine)
    r = spine.read().execute(
        f"SELECT {_TYPE_COLS} FROM obligation_types WHERE kind=?", (kind,)
    ).fetchone()
    return dict(r) if r else None


def active_kinds(spine) -> list[str]:
    return [t["kind"] for t in list_types(spine, active_only=True)]


def upsert_type(spine, kind: str, label: str, rule: str, frequency: str,
                applies_to: str, active: bool = True, sort: int = 100,
                description: str = "", user: str = "?") -> str:
    """Kreira ili uređuje vrstu obveze. kind se normalizira na VELIKA slova
    (stabilan ključ). Vraća normalizirani kind."""
    kind = (kind or "").strip().upper()
    if not kind:
        raise ValueError("kind je obavezan")
    if frequency not in FREQUENCIES:
        raise ValueError(f"nepoznata frekvencija: {frequency!r}")
    if applies_to not in APPLIES_TO:
        raise ValueError(f"nepoznat applies_to: {applies_to!r}")
    rule = (rule or "").strip()
    if rule:
        rf = rule.split(":", 1)[0]
        if rf not in FREQUENCIES:
            raise ValueError(f"nepravilno pravilo roka: {rule!r}")
        if rf != frequency:
            raise ValueError(f"pravilo ({rf}) ne odgovara frekvenciji ({frequency})")
    label = (label or "").strip() or kind
    _ensure_seeded(spine)
    with spine.write() as c:
        c.execute(
            """INSERT INTO obligation_types
                 (kind, label, rule, frequency, applies_to, active, sort, description)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(kind) DO UPDATE SET
                 label=excluded.label, rule=excluded.rule, frequency=excluded.frequency,
                 applies_to=excluded.applies_to, active=excluded.active,
                 sort=excluded.sort, description=excluded.description""",
            (kind, label, rule, frequency, applies_to, int(bool(active)), sort, description),
        )
    spine.audit(user, "obligation_type_upsert", f"type:{kind}")
    return kind


def set_client_types(spine, client_id: int, kinds: list[str], user: str = "?") -> None:
    """Zamjenjuje set 'manual' vrsta koje klijent ima (npr. najam)."""
    with spine.write() as c:
        c.execute("DELETE FROM client_obligation_types WHERE client_id=?", (client_id,))
        for k in kinds:
            c.execute(
                "INSERT OR IGNORE INTO client_obligation_types(client_id, kind) VALUES(?,?)",
                (client_id, k),
            )
    spine.audit(user, "client_obligation_types_set", f"client:{client_id}")


def client_types(spine, client_id: int) -> list[str]:
    return [r["kind"] for r in spine.read().execute(
        "SELECT kind FROM client_obligation_types WHERE client_id=?", (client_id,)
    ).fetchall()]


def _obligor_ids(spine, otype: dict, period: str) -> list[int]:
    """Klijenti koji za ovu vrstu i period imaju obvezu, po applies_to pravilu."""
    applies = otype["applies_to"]
    month = int(period[5:7])
    c = spine.read()

    # Type-frekvencija gate vrijedi za SVE vrste (uklj. custom pdv+yearly):
    # kvartalna/godišnja vrsta postoji samo u mjesecu predaje.
    if otype["frequency"] == "quarterly" and month not in _QUARTER_MONTHS:
        return []
    if otype["frequency"] == "yearly" and month != _yearly_month(otype["rule"]):
        return []

    if applies == "pdv":
        # "u sustavu PDV-a" DA, ali NE "nije u sustavu PDV-a" (LIKE '%u sustavu%'
        # bi inače uhvatio i negativnu vrijednost).
        rows = c.execute(
            "SELECT id, pdv_freq FROM clients WHERE active=1 "
            "AND lower(pdv_status) LIKE '%u sustavu%' AND lower(pdv_status) NOT LIKE '%nije%'"
        ).fetchall()
        ids = []
        for r in rows:
            # per-klijent frekvencija: tromjesečni obveznik samo u kvartalnim mjesecima
            if (r["pdv_freq"] or "monthly") == "quarterly" and month not in _QUARTER_MONTHS:
                continue
            ids.append(r["id"])
        return ids

    if applies == "employees":
        return [r["id"] for r in c.execute(
            "SELECT id FROM clients WHERE active=1 AND has_employees=1").fetchall()]
    if applies in _REGIME_APPLIES:
        return [r["id"] for r in c.execute(
            "SELECT id FROM clients WHERE active=1 AND regime=?", (applies,)).fetchall()]
    if applies == "all_active":
        return [r["id"] for r in c.execute(
            "SELECT id FROM clients WHERE active=1").fetchall()]
    if applies == "manual":
        return [r["id"] for r in c.execute(
            """SELECT c.id FROM clients c
               JOIN client_obligation_types t ON t.client_id = c.id
               WHERE c.active=1 AND t.kind=?""", (otype["kind"],)).fetchall()]
    return []


def ensure_period(spine, kind: str, period: str) -> None:
    """Kreira obligations red za svakog obveznika te vrste u tom periodu.
    Idempotentno (INSERT OR IGNORE + UNIQUE(client_id,kind,period))."""
    otype = get_type(spine, kind)
    if otype is None:
        raise ValueError(f"Nepoznat kind obveze: {kind!r}")
    ids = _obligor_ids(spine, otype, period)
    id_set = set(ids)
    with spine.write() as c:
        for cid in ids:
            c.execute(
                "INSERT OR IGNORE INTO obligations(client_id, kind, period) VALUES(?,?,?)",
                (cid, kind, period),
            )
        # Ukloni zastarjele NEPOSLANE obveze (klijent više nije obveznik — izgubio
        # zaposlene, promijenio sustav, postao neaktivan, maknut manual...). Poslane
        # (sent=1) čuvamo kao povijest.
        stale = c.execute(
            """SELECT o.id, o.client_id FROM obligations o
               LEFT JOIN obligation_status s ON s.obligation_id = o.id
               WHERE o.kind=? AND o.period=? AND COALESCE(s.sent, 0) = 0""",
            (kind, period),
        ).fetchall()
        for r in stale:
            if r["client_id"] not in id_set:
                c.execute("DELETE FROM obligation_status WHERE obligation_id=?", (r["id"],))
                c.execute("DELETE FROM obligations WHERE id=?", (r["id"],))


def list_period(spine, kind: str, period: str) -> list[dict]:
    rows = spine.read().execute(
        """SELECT o.id AS obligation_id, c.name AS client,
                  COALESCE(s.sent, 0) AS sent, s.sent_by AS sent_by, s.sent_at AS sent_at
           FROM obligations o
           JOIN clients c ON c.id = o.client_id
           LEFT JOIN obligation_status s ON s.obligation_id = o.id
           WHERE o.kind = ? AND o.period = ?
           ORDER BY sent, c.name COLLATE NOCASE""",
        (kind, period),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(spine, obligation_id: int, user: str, sent: bool = True) -> None:
    with spine.write() as c:
        if c.execute("SELECT 1 FROM obligations WHERE id=?", (obligation_id,)).fetchone() is None:
            raise ValueError(f"nepoznata obveza: {obligation_id}")
        c.execute(
            """INSERT INTO obligation_status(obligation_id, sent, sent_by, sent_at)
               VALUES(?,?,?,datetime('now'))
               ON CONFLICT(obligation_id) DO UPDATE SET
                 sent=excluded.sent, sent_by=excluded.sent_by, sent_at=excluded.sent_at""",
            (obligation_id, int(sent), user),
        )
    spine.audit(user, "obligation_sent" if sent else "obligation_unsent", f"obligation:{obligation_id}")
