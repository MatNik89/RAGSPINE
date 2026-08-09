"""Faza 5: radnička flota — tokeni uređaja, program-allowlista, red naredbi.

Token = "<device_id>.<secret>": device_id je javan pokazivač, hash tajne se
sprema po device_id -> verify je O(1) (bez enumeracije/timing curenja). Server
NIKAD ne šalje naredbeni string agentu — samo `program_key` iz allowliste; agent
drži vlastitu key->argv mapu i odbija nepoznato.
"""
import hashlib
import json
import re
import secrets

# Tvrda allowlista radnji — i server i agent je provjeravaju
ACTIONS = ("run_program", "shutdown", "enable_wol", "status")


def _digest(secret: str) -> str:
    # Tajna je nasumičnih 256 bita -> PBKDF2 (spor, anti-bruteforce) NIJE potreban
    # i stvara CPU-DoS amplifikaciju po pollu; SHA-256 + constant-time usporedba
    # je dovoljna i brza (Codex T1 nalaz).
    return hashlib.sha256(secret.encode()).hexdigest()


def _norm_key(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("key mora biti string")
    k = key.strip().lower()
    for a, b in zip("čćžšđ", "cczsd"):
        k = k.replace(a, b)
    k = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
    if not k:
        raise ValueError("key je obavezan")
    return k


# --- tokeni ----------------------------------------------------------------

def issue_token(spine, device_id: int) -> str:
    """Izdaj (ili rotiraj) token uređaja. Plaintext se vraća JEDNOM."""
    with spine.write() as c:
        if c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone() is None:
            raise ValueError(f"nepoznat uređaj: {device_id}")
        secret = secrets.token_urlsafe(32)
        c.execute(
            """INSERT INTO device_tokens(device_id, token_hash, revoked) VALUES(?,?,0)
               ON CONFLICT(device_id) DO UPDATE SET token_hash=excluded.token_hash,
               revoked=0, created_at=datetime('now')""",
            (device_id, _digest(secret)))
    return f"{device_id}.{secret}"


def revoke_token(spine, device_id: int) -> None:
    with spine.write() as c:
        c.execute("UPDATE device_tokens SET revoked=1 WHERE device_id=?", (device_id,))


def verify_token(spine, token: str):
    """Vrati device_id ako token vrijedi i nije opozvan, inače None (svježe)."""
    if not token or "." not in token:
        return None
    did_s, _, secret = token.partition(".")
    try:
        did = int(did_s)
    except ValueError:
        return None
    row = spine.read().execute(
        "SELECT token_hash FROM device_tokens WHERE device_id=? AND revoked=0",
        (did,)).fetchone()
    if row is None or not secrets.compare_digest(_digest(secret), row["token_hash"] or ""):
        return None
    return did


# --- program allowlist -----------------------------------------------------

def add_program(spine, key: str, label: str, user: str = "?") -> str:
    key = _norm_key(key)
    label = (label or "").strip() or key
    with spine.write() as c:
        c.execute(
            """INSERT INTO fleet_programs(key, label, added_by) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET label=excluded.label""",
            (key, label, user))
    return key


def list_programs(spine) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        "SELECT key, label, added_by, added_at FROM fleet_programs ORDER BY key").fetchall()]


def remove_program(spine, key: str) -> None:
    key = _norm_key(key)
    with spine.write() as c:
        c.execute("DELETE FROM fleet_programs WHERE key=?", (key,))
        # ownerovo opozivanje mora odmah otkazati već enqueueane naredbe tog
        # programa — inače admin unaprijed napuni red pa opoziv nema učinka
        c.execute("UPDATE agent_commands SET status='cancelled' "
                  "WHERE action='run_program' AND program_key=? AND status='pending'", (key,))


# --- red naredbi -----------------------------------------------------------

def enqueue(spine, device_id: int, action: str, program_key: str | None = None) -> int:
    if action not in ACTIONS:
        raise ValueError(f"nedozvoljena radnja: {action!r}")
    with spine.write() as c:
        if c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone() is None:
            raise ValueError(f"nepoznat uređaj: {device_id}")
        if action == "run_program":
            program_key = _norm_key(program_key or "")
            if c.execute("SELECT 1 FROM fleet_programs WHERE key=?", (program_key,)).fetchone() is None:
                raise ValueError(f"program nije na allowlisti: {program_key!r}")
        else:
            program_key = None
        return c.execute(
            "INSERT INTO agent_commands(device_id, action, program_key) VALUES(?,?,?)",
            (device_id, action, program_key)).lastrowid


def next_command(spine, device_id: int) -> dict | None:
    """Najstariji pending za uređaj -> in_progress. JEDNA atomska claim naredba
    (UPDATE ... RETURNING) — dva istovremena polla ne mogu dobiti istu naredbu
    (drugi UPDATE ne pogodi red jer više nije 'pending'). (Codex T1 nalaz.)"""
    with spine.write() as c:
        while True:
            row = c.execute(
                "UPDATE agent_commands SET status='in_progress' "
                "WHERE id=(SELECT id FROM agent_commands WHERE device_id=? AND status='pending' "
                "ORDER BY id LIMIT 1) AND status='pending' "
                "RETURNING id, action, program_key",
                (device_id,)).fetchone()
            if row is None:
                return None
            # claim-time re-provjera allowliste: ako je program u međuvremenu
            # maknut, NE isporuči ga (ownerov opoziv je autoritativan)
            if row["action"] == "run_program" and c.execute(
                    "SELECT 1 FROM fleet_programs WHERE key=?", (row["program_key"],)).fetchone() is None:
                c.execute(
                    "UPDATE agent_commands SET status='cancelled', result=?, done_at=datetime('now') "
                    "WHERE id=?",
                    (json.dumps({"ok": False, "detail": "program uklonjen s allowliste"}), row["id"]))
                continue  # preskoči, uzmi idući pending
            return {"id": row["id"], "action": row["action"], "program_key": row["program_key"]}


_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


def _worker_matches(worker_username: str, name: str) -> bool:
    """Labavo poklapanje imena radnika (podnosi hrvatsku deklinaciju: 'Ane'->
    'ana'): jedno je prefiks drugog ili dijele prve 3 slova."""
    import os
    a, b = worker_username.lower(), name.lower()
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    # deklinacija (Ana/Ane/Ani): dijele sve osim zadnjeg slova
    cp = len(os.path.commonprefix([a, b]))
    return cp >= 2 and cp >= min(len(a), len(b)) - 1


def open_on_worker(spine, worker_name: str, program_query: str, actor_role: str) -> dict:
    """Chat 'kod <ime> otvori <program>': nađi radnikovu stanicu + program iz
    allowliste i enqueue run_program. Admin+ (dnevno pokretanje). Vrati
    {ok, message, command_id?}."""
    from atlas.business import devices as devices_mod

    if _ROLE_RANK.get(actor_role, -1) < _ROLE_RANK["admin"]:
        return {"ok": False, "message": "Za pokretanje programa na radnoj stanici potrebna je admin uloga."}

    workers = [d for d in devices_mod.list_devices(spine)
               if d.get("worker_username") and _worker_matches(d["worker_username"], worker_name)
               and d.get("host")]
    if len(workers) != 1:
        which = "nijedan" if not workers else "više"
        return {"ok": False, "message": f"Ne mogu jednoznačno odrediti stanicu za {worker_name!r} ({which})."}

    pq = _norm_key(program_query)
    progs = [p for p in list_programs(spine)
             if pq == p["key"] or pq in p["key"] or pq in _norm_key(p["label"])]
    if len(progs) != 1:
        return {"ok": False, "message": f"Program {program_query!r} nije jednoznačno na allowlisti."}

    cid = enqueue(spine, workers[0]["id"], "run_program", program_key=progs[0]["key"])
    return {"ok": True, "command_id": cid,
            "message": f"Pokrećem {progs[0]['label']} na {workers[0]['name']}."}


_FLOTA_RE = re.compile(r"kod\s+(\S+)\s+otvori\s+(.+)", re.IGNORECASE)


def flota_handle(spine, cfg, query: str, llm=None, actor=None) -> str:
    """Chat lane: 'kod <ime> otvori <program>'. Actor-threaded (admin-gate)."""
    role = getattr(actor, "role", None)
    m = _FLOTA_RE.search(query)
    if not m:
        return "Reci: kod <ime radnika> otvori <program>."
    return open_on_worker(spine, m.group(1).strip(), m.group(2).strip(),
                          actor_role=role or "viewer")["message"]


from atlas.rag import pipeline  # noqa: E402 (lazy: izbjegni import-order coupling)
pipeline.LANE_HANDLERS["flota"] = flota_handle


def complete(spine, cmd_id: int, device_id: int, result) -> bool:
    """Zatvori naredbu SAMO ako je 'in_progress' i pripada uređaju — spriječi da
    agent preskoči izvršenje (pending->done) ili prepiše već završen rezultat.
    Vrati True ako je red stvarno zatvoren."""
    with spine.write() as c:
        cur = c.execute(
            "UPDATE agent_commands SET status='done', result=?, done_at=datetime('now') "
            "WHERE id=? AND device_id=? AND status='in_progress'",
            (json.dumps(result, ensure_ascii=False, default=str), cmd_id, device_id))
        return cur.rowcount == 1
