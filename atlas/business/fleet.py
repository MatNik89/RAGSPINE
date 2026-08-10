"""Faza 5: radnička flota — tokeni uređaja, program-allowlista, red naredbi.

Token = "<device_id>.<secret>": device_id je javan pokazivač, hash tajne se
sprema po device_id -> verify je O(1) (bez enumeracije/timing curenja). Server
NIKAD ne šalje naredbeni string agentu — samo `program_key` iz allowliste; agent
drži vlastitu key->argv mapu i odbija nepoznato.
"""
import hashlib
import hmac
import json
import re
import secrets

# Tvrda allowlista radnji — i server i agent je provjeravaju
ACTIONS = ("run_program", "shutdown", "enable_wol", "status")


def _master_signing_key(spine, cfg) -> str:
    """Per-instalacija MASTER ključ (tajna, ne izlazi iz servera). Atomičan
    get-or-create (INSERT OR IGNORE pa re-read) — dvije paralelne prve izdaje
    ne mogu dati različite ključeve (Codex nalaz)."""
    from atlas.business import secretbox
    with spine.write() as c:
        # master ključ se sprema ŠIFRIRAN (secretbox, ključ iz jwt_secreta u datoteci,
        # ne u bazi) — ukradeni DB backup ne otkriva ključ za potpis naredbi (Codex nalaz)
        c.execute("INSERT OR IGNORE INTO config_overrides(module,key,value,updated_at) "
                  "VALUES('fleet','signing_key',?,datetime('now'))",
                  (secretbox.encrypt(secrets.token_urlsafe(32), cfg),))
    raw = spine.get_override("fleet", "signing_key", "")
    val = secretbox.decrypt(raw, cfg)  # fallback vraća stari plaintext zapis kakav jest
    if raw and not raw.startswith("enc:"):  # migracija: stari plaintext -> šifriraj u mjestu
        spine.set_override("fleet", "signing_key", secretbox.encrypt(val, cfg))
    return val


def device_sign_key(spine, device_id: int, cfg) -> str:
    """PER-UREĐAJ ključ = HMAC(master, device_id). Kompromitacija jednog uređaja
    ne daje mogućnost potpisa za DRUGI (master nikad ne napušta server); opoziv
    je vezan uz uređaj (Codex nalaz: dijeljeni simetrični ključ = fleet-wide forge)."""
    return hmac.new(_master_signing_key(spine, cfg).encode(), str(device_id).encode(),
                    hashlib.sha256).hexdigest()


def _canon(device_id: int, cmd: dict) -> bytes:
    # potpis veže i device_id i id (monotoni, anti-replay se provjerava kod agenta)
    return json.dumps({"device_id": int(device_id), "id": cmd.get("id"),
                       "action": cmd.get("action"), "program_key": cmd.get("program_key")},
                      sort_keys=True, separators=(",", ":")).encode()


def sign_command(spine, device_id: int, cmd: dict, cfg) -> str:
    return hmac.new(device_sign_key(spine, device_id, cfg).encode(),
                    _canon(device_id, cmd), hashlib.sha256).hexdigest()


# --- samo-prijava + odobri (agent se javi kao "na čekanju", owner odobri) ----

_ENROLL_TTL_MIN = 30       # zahtjev istekne ako ga owner ne odobri
_ENROLL_MAX_PENDING = 50   # globalni cap protiv DB-DoS-a javnim endpointom
_ENROLL_MAX_PER_SRC = 5    # po-izvoru (IP) cap: jedan host ne puni cijeli red
_ENROLL_WINDOW_MIN = 15    # koliko dugo ostaje otvoreno "sparivanje" nakon što owner klikne
_EXPIRED = f"datetime('now', '-{_ENROLL_TTL_MIN} minutes')"


def open_enrollment(spine, user: str, minutes: int = _ENROLL_WINDOW_MIN) -> str:
    """Owner otvori prozor za sparivanje. BEZ otvorenog prozora javni /agent/enroll
    je zatvoren (fail-closed) — neautentificirani LAN host ne može ni puniti red ni
    provocirati DoS dok owner ne kaže 'sad dodajem računalo' (Codex nalaz)."""
    m = max(1, min(int(minutes), 120))
    until = spine.read().execute(
        "SELECT datetime('now', ?) AS t", (f"+{m} minutes",)).fetchone()["t"]
    spine.set_override("enroll", "open_until", until)
    spine.audit(user, "agent_enroll_open", "enroll", f"{m} min")
    return until


def enrollment_open(spine) -> bool:
    until = spine.get_override("enroll", "open_until", None)
    if not until:
        return False
    return spine.read().execute(
        "SELECT (datetime('now') < ?) AS ok", (until,)).fetchone()["ok"] == 1


def enroll_request(spine, device_name: str, source: str = "") -> tuple[str, str]:
    """Agent traži upis. Vrati (enroll_id, secret) — agent čuva secret i njime
    kasnije preuzme kredencijale nakon što owner odobri. NIŠTA se ne izda dok
    owner ne klikne Odobri."""
    if not enrollment_open(spine):
        raise ValueError("sparivanje nije otvoreno — zamolite vlasnika da otvori upis")
    src = (source or "")[:64]
    enroll_id = secrets.token_urlsafe(12)
    secret = secrets.token_urlsafe(24)
    with spine.write() as c:
        # cleanup: makni preuzete i ISTEKLE PENDING (istek vrijedi SAMO za pending —
        # inače bi javni endpoint obrisao approved-neuzet red i ostavio siroti
        # uređaj/token; Codex nalaz)
        c.execute(f"DELETE FROM agent_enrollments WHERE status='consumed' "
                  f"OR (status='pending' AND created_at < {_EXPIRED})")
        # per-source cap: jedan LAN host ne može sam ispuniti globalni red i tako
        # blokirati legitimno sparivanje unutar prozora (Codex #5)
        if src:
            per = c.execute("SELECT COUNT(*) AS n FROM agent_enrollments "
                            "WHERE status='pending' AND source=?", (src,)).fetchone()["n"]
            if per >= _ENROLL_MAX_PER_SRC:
                raise ValueError("previše zahtjeva s ovog računala — pokušajte kasnije")
        n = c.execute("SELECT COUNT(*) AS n FROM agent_enrollments WHERE status='pending'").fetchone()["n"]
        if n >= _ENROLL_MAX_PENDING:
            raise ValueError("previše zahtjeva na čekanju — pokušajte kasnije")
        c.execute("INSERT INTO agent_enrollments(id, secret_hash, device_name, status, source) "
                  "VALUES(?,?,?,'pending',?)",
                  (enroll_id, _digest(secret), (device_name or "Radna stanica").strip()[:80], src))
    return enroll_id, secret


def list_pending_enrollments(spine, limit: int = 100) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        f"SELECT id, device_name, created_at FROM agent_enrollments "
        f"WHERE status='pending' AND created_at >= {_EXPIRED} "
        f"ORDER BY created_at LIMIT ?", (limit,)).fetchall()]


def approve_enrollment(spine, cfg, enroll_id: str, device_name: str | None, user: str) -> int:
    """Owner odobri: kreira uređaj, izda token + sign_key, spremi ih ŠIFRIRANO
    uz enrollment (agent ih preuzme jednom). Vrati device_id."""
    from atlas.business import devices, secretbox
    # 1) atomično preuzmi red pending -> approving. Uvjetni UPDATE + rowcount==1
    #    znači da SAMO jedan istovremeni approve prođe (nema TOCTOU dvostrukog
    #    izdavanja), i istekli pending se odbija ovdje (Codex nalaz).
    with spine.write() as c:
        row = c.execute("SELECT device_name FROM agent_enrollments WHERE id=?",
                        (enroll_id,)).fetchone()
        if row is None:
            raise ValueError("nepoznat zahtjev za upis")
        expired = c.execute(
            f"SELECT 1 FROM agent_enrollments WHERE id=? AND status='pending' "
            f"AND created_at < {_EXPIRED}", (enroll_id,)).fetchone()
        if expired is not None:
            raise ValueError("zahtjev za upis je istekao")
        claim = c.execute("UPDATE agent_enrollments SET status='approving' "
                          "WHERE id=? AND status='pending'", (enroll_id,))
        if claim.rowcount != 1:
            raise ValueError("zahtjev je već obrađen")
    # 2) sada jedinstveni vlasnik reda -> kreiraj uređaj + kredencijale, pa finaliziraj
    name = (device_name or row["device_name"] or "Radna stanica").strip()
    dev = devices.add_device(spine, "radna-stanica", name, user=user)
    token = issue_token(spine, dev["id"])
    signk = device_sign_key(spine, dev["id"], cfg)
    with spine.write() as c:
        c.execute("UPDATE agent_enrollments SET status='approved', device_id=?, "
                  "token_enc=?, signkey_enc=? WHERE id=? AND status='approving'",
                  (dev["id"], secretbox.encrypt(token, cfg), secretbox.encrypt(signk, cfg), enroll_id))
    spine.audit(user, "agent_enroll_approve", f"device:{dev['id']}", name)
    return dev["id"]


def poll_enrollment(spine, cfg, enroll_id: str, secret: str) -> dict:
    """Agent poll: {status:'pending'} dok owner ne odobri; kad odobri vrati
    token+sign_key JEDNOM (pa označi consumed). Kriv secret/nepoznat -> greška."""
    from atlas.business import secretbox
    row = spine.read().execute(
        "SELECT secret_hash, status, device_id, token_enc, signkey_enc "
        "FROM agent_enrollments WHERE id=?", (enroll_id,)).fetchone()
    if row is None or not secrets.compare_digest(_digest(secret or ""), row["secret_hash"] or ""):
        raise ValueError("nevažeći zahtjev za upis")
    if row["status"] in ("pending", "approving"):  # 'approving' = owner upravo izdaje
        if row["status"] == "pending":
            exp = spine.read().execute(
                f"SELECT 1 FROM agent_enrollments WHERE id=? AND created_at < {_EXPIRED}",
                (enroll_id,)).fetchone()
            if exp is not None:
                raise ValueError("zahtjev za upis je istekao")
        return {"status": "pending"}
    if row["status"] != "approved":
        raise ValueError("kredencijali su već preuzeti")
    with spine.write() as c:  # jednokratno: approved -> consumed
        cur = c.execute("UPDATE agent_enrollments SET status='consumed' "
                        "WHERE id=? AND status='approved'", (enroll_id,))
        if cur.rowcount != 1:
            raise ValueError("kredencijali su već preuzeti")
    return {"status": "approved", "device_id": row["device_id"],
            "token": secretbox.decrypt(row["token_enc"], cfg),
            "sign_key": secretbox.decrypt(row["signkey_enc"], cfg)}


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


def device_activity(spine, device_id: int, limit: int = 30) -> list[dict]:
    """Nedavna aktivnost jednog uređaja (naredbe: akcija/status/rezultat/vrijeme).
    Za 'uživo' prikaz vlasnik samo poll-a ovaj endpoint (ponytail: bez wss-a —
    long-poll/poll pokriva potrebu; upgrade path = SSE/wss ako zatreba niža
    latencija). Read-only."""
    rows = spine.read().execute(
        "SELECT id, action, program_key, status, result, created_at, done_at "
        "FROM agent_commands WHERE device_id=? ORDER BY id DESC LIMIT ?",
        (device_id, max(1, min(int(limit), 200)))).fetchall()
    return [dict(r) for r in rows]


def wake_worker(spine, worker_name: str, actor_role: str, sender=None) -> dict:
    """Probudi radnikovu stanicu (Wake-on-LAN). Admin+ (kao pokretanje programa).
    Jednoznačno razriješi radnika s MAC-om, pošalji magic paket. `sender`
    injektabilan za testove."""
    from atlas.business import devices as devices_mod
    from atlas.core import wol

    if _ROLE_RANK.get(actor_role, -1) < _ROLE_RANK["admin"]:
        return {"ok": False, "message": "Za buđenje stanice potrebna je admin uloga."}
    workers = [d for d in devices_mod.list_devices(spine)
               if d.get("worker_username") and _worker_matches(d["worker_username"], worker_name)
               and d.get("mac")]
    if len(workers) != 1:
        which = "nijedna (ili nema MAC)" if not workers else "više njih"
        return {"ok": False, "message": f"Ne mogu jednoznačno odrediti stanicu za {worker_name!r} ({which})."}
    woken = wol.wake_fleet(workers, sender=sender)
    if not woken:
        return {"ok": False, "message": f"Neispravan MAC za {workers[0]['name']}."}
    spine.audit("agent", "wake_worker", f"device:{workers[0]['id']}", workers[0]["name"])
    return {"ok": True, "message": f"Šaljem signal za buđenje: {workers[0]['name']}."}


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
