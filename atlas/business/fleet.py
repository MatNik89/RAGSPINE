"""Phase 5: worker fleet — device tokens, program allowlist, command queue.

Token = "<device_id>.<secret>": device_id is a public pointer, the secret's hash is
stored by device_id -> verify is O(1) (no enumeration/timing leak). The server
NEVER sends a command string to the agent — only a `program_key` from the allowlist;
the agent keeps its own key->argv map and rejects anything unknown.
"""
import hashlib
import hmac
import json
import re
import secrets

# Hard allowlist of actions — both server and agent check it
ACTIONS = ("run_program", "shutdown", "enable_wol", "status")


def _master_signing_key(spine, cfg) -> str:
    """Per-installation MASTER key (secret, never leaves the server). Atomic
    get-or-create (INSERT OR IGNORE then re-read) — two parallel first issuances
    cannot produce different keys (Codex finding)."""
    from atlas.business import secretbox
    with spine.write() as c:
        # the master key is stored ENCRYPTED (secretbox, key from jwt_secret in a file,
        # not in the DB) — a stolen DB backup does not reveal the command-signing key (Codex finding)
        c.execute("INSERT OR IGNORE INTO config_overrides(module,key,value,updated_at) "
                  "VALUES('fleet','signing_key',?,datetime('now'))",
                  (secretbox.encrypt(secrets.token_urlsafe(32), cfg),))
    raw = spine.get_override("fleet", "signing_key", "")
    val = secretbox.decrypt(raw, cfg)  # fallback returns the old plaintext record as-is
    if raw.startswith("enc:") and not val:
        # decryption failed (e.g. jwt_secret diverged) -> do NOT sign with an empty key
        # (otherwise silent fleet-lockout / falsely valid signatures); fail-closed (Codex finding)
        raise RuntimeError("master signing_key se ne može dešifrirati (provjeri jwt_secret)")
    if raw and not raw.startswith("enc:"):  # migration: old plaintext -> encrypt in place
        spine.set_override("fleet", "signing_key", secretbox.encrypt(val, cfg))
    return val


def device_sign_key(spine, device_id: int, cfg) -> str:
    """PER-DEVICE key = HMAC(master, device_id). Compromising one device does
    not grant the ability to sign for ANOTHER (the master never leaves the server);
    revocation is tied to the device (Codex finding: a shared symmetric key = fleet-wide forge)."""
    return hmac.new(_master_signing_key(spine, cfg).encode(), str(device_id).encode(),
                    hashlib.sha256).hexdigest()


def _canon(device_id: int, cmd: dict) -> bytes:
    # the signature binds both device_id and id (monotonic, anti-replay is checked at the agent)
    return json.dumps({"device_id": int(device_id), "id": cmd.get("id"),
                       "action": cmd.get("action"), "program_key": cmd.get("program_key")},
                      sort_keys=True, separators=(",", ":")).encode()


def sign_command(spine, device_id: int, cmd: dict, cfg) -> str:
    return hmac.new(device_sign_key(spine, device_id, cfg).encode(),
                    _canon(device_id, cmd), hashlib.sha256).hexdigest()


# --- self-enrollment + approve (agent reports in as "pending", owner approves) ----

_ENROLL_TTL_MIN = 30       # request expires if the owner does not approve it
_ENROLL_MAX_PENDING = 50   # global cap against DB-DoS via the public endpoint
_ENROLL_MAX_PER_SRC = 5    # per-source (IP) cap: one host cannot fill the whole queue
_ENROLL_WINDOW_MIN = 15    # how long "pairing" stays open after the owner clicks
_EXPIRED = f"datetime('now', '-{_ENROLL_TTL_MIN} minutes')"


def open_enrollment(spine, user: str, minutes: int = _ENROLL_WINDOW_MIN) -> str:
    """Owner opens the pairing window. WITHOUT an open window the public /agent/enroll
    is closed (fail-closed) — an unauthenticated LAN host can neither fill the queue nor
    provoke a DoS until the owner says 'now I'm adding a computer' (Codex finding)."""
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
    """Agent requests enrollment. Return (enroll_id, secret) — the agent keeps the secret
    and later uses it to pick up credentials once the owner approves. NOTHING is issued
    until the owner clicks Approve."""
    if not enrollment_open(spine):
        raise ValueError("sparivanje nije otvoreno — zamolite vlasnika da otvori upis")
    src = (source or "")[:64]
    enroll_id = secrets.token_urlsafe(12)
    secret = secrets.token_urlsafe(24)
    with spine.write() as c:
        # cleanup: remove consumed and EXPIRED PENDING (expiry applies ONLY to pending —
        # otherwise the public endpoint would delete an approved-unclaimed row and leave an
        # orphaned device/token; Codex finding)
        c.execute(f"DELETE FROM agent_enrollments WHERE status='consumed' "
                  f"OR (status='pending' AND created_at < {_EXPIRED})")
        # per-source cap: one LAN host cannot fill the global queue by itself and thereby
        # block legitimate pairing within the window (Codex #5)
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
    """Owner approves: creates the device, issues token + sign_key, stores them ENCRYPTED
    alongside the enrollment (the agent picks them up once). Return device_id."""
    from atlas.business import devices, secretbox
    # 1) atomically claim the row pending -> approving. Conditional UPDATE + rowcount==1
    #    means ONLY one concurrent approve passes (no TOCTOU double
    #    issuance), and an expired pending is rejected here (Codex finding).
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
    # 2) now the sole owner of the row -> create device + credentials, then finalize
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
    """Agent poll: {status:'pending'} until the owner approves; on approval return
    token+sign_key ONCE (then mark consumed). Wrong secret/unknown -> error."""
    from atlas.business import secretbox
    row = spine.read().execute(
        "SELECT secret_hash, status, device_id, token_enc, signkey_enc "
        "FROM agent_enrollments WHERE id=?", (enroll_id,)).fetchone()
    if row is None or not secrets.compare_digest(_digest(secret or ""), row["secret_hash"] or ""):
        raise ValueError("nevažeći zahtjev za upis")
    if row["status"] in ("pending", "approving"):  # 'approving' = owner is issuing right now
        if row["status"] == "pending":
            exp = spine.read().execute(
                f"SELECT 1 FROM agent_enrollments WHERE id=? AND created_at < {_EXPIRED}",
                (enroll_id,)).fetchone()
            if exp is not None:
                raise ValueError("zahtjev za upis je istekao")
        return {"status": "pending"}
    if row["status"] != "approved":
        raise ValueError("kredencijali su već preuzeti")
    with spine.write() as c:  # one-shot: approved -> consumed
        cur = c.execute("UPDATE agent_enrollments SET status='consumed' "
                        "WHERE id=? AND status='approved'", (enroll_id,))
        if cur.rowcount != 1:
            raise ValueError("kredencijali su već preuzeti")
    return {"status": "approved", "device_id": row["device_id"],
            "token": secretbox.decrypt(row["token_enc"], cfg),
            "sign_key": secretbox.decrypt(row["signkey_enc"], cfg)}


def _digest(secret: str) -> str:
    # The secret is a random 256 bits -> PBKDF2 (slow, anti-bruteforce) is NOT needed
    # and would create CPU-DoS amplification per poll; SHA-256 + constant-time comparison
    # is sufficient and fast (Codex T1 finding).
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
    """Issue (or rotate) a device token. The plaintext is returned ONCE."""
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
    """Return device_id if the token is valid and not revoked, otherwise None (fresh)."""
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
        # the owner's revocation must immediately cancel already-enqueued commands for that
        # program — otherwise an admin pre-fills the queue and the revocation has no effect
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
    """Oldest pending for the device -> in_progress. ONE atomic claim statement
    (UPDATE ... RETURNING) — two concurrent polls cannot get the same command
    (the second UPDATE misses the row because it is no longer 'pending'). (Codex T1 finding.)"""
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
            # claim-time re-check of the allowlist: if the program was removed in the
            # meantime, do NOT deliver it (the owner's revocation is authoritative)
            if row["action"] == "run_program" and c.execute(
                    "SELECT 1 FROM fleet_programs WHERE key=?", (row["program_key"],)).fetchone() is None:
                c.execute(
                    "UPDATE agent_commands SET status='cancelled', result=?, done_at=datetime('now') "
                    "WHERE id=?",
                    (json.dumps({"ok": False, "detail": "program uklonjen s allowliste"}), row["id"]))
                continue  # skip, take the next pending
            return {"id": row["id"], "action": row["action"], "program_key": row["program_key"]}


_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


def _worker_matches(worker_username: str, name: str) -> bool:
    """Loose matching of a worker's name (tolerates Croatian declension: 'Ane'->
    'ana'): one is a prefix of the other or they share the first 3 letters."""
    import os
    a, b = worker_username.lower(), name.lower()
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    # declension (Ana/Ane/Ani): they share everything but the last letter
    cp = len(os.path.commonprefix([a, b]))
    return cp >= 2 and cp >= min(len(a), len(b)) - 1


def open_on_worker(spine, worker_name: str, program_query: str, actor_role: str) -> dict:
    """Chat 'kod <name> otvori <program>': find the worker's station + program from
    the allowlist and enqueue run_program. Admin+ (daily launch). Return
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
    """Recent activity of a single device (commands: action/status/result/time).
    For a 'live' view the owner just polls this endpoint (ponytail: no wss —
    long-poll/poll covers the need; upgrade path = SSE/wss if lower latency is
    needed). Read-only."""
    rows = spine.read().execute(
        "SELECT id, action, program_key, status, result, created_at, done_at "
        "FROM agent_commands WHERE device_id=? ORDER BY id DESC LIMIT ?",
        (device_id, max(1, min(int(limit), 200)))).fetchall()
    return [dict(r) for r in rows]


def wake_worker(spine, worker_name: str, actor_role: str, sender=None) -> dict:
    """Wake the worker's station (Wake-on-LAN). Admin+ (like launching programs).
    Unambiguously resolve the worker with a MAC, send the magic packet. `sender`
    is injectable for tests."""
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
    """Chat lane: 'kod <name> otvori <program>'. Actor-threaded (admin-gate)."""
    role = getattr(actor, "role", None)
    m = _FLOTA_RE.search(query)
    if not m:
        return "Reci: kod <ime radnika> otvori <program>."
    return open_on_worker(spine, m.group(1).strip(), m.group(2).strip(),
                          actor_role=role or "viewer")["message"]


from atlas.rag import pipeline  # noqa: E402 (lazy: avoid import-order coupling)
pipeline.LANE_HANDLERS["flota"] = flota_handle


def complete(spine, cmd_id: int, device_id: int, result) -> bool:
    """Close the command ONLY if it is 'in_progress' and belongs to the device — prevent
    the agent from skipping execution (pending->done) or overwriting an already-finished result.
    Return True if the row was actually closed."""
    with spine.write() as c:
        cur = c.execute(
            "UPDATE agent_commands SET status='done', result=?, done_at=datetime('now') "
            "WHERE id=? AND device_id=? AND status='in_progress'",
            (json.dumps(result, ensure_ascii=False, default=str), cmd_id, device_id))
        return cur.rowcount == 1
