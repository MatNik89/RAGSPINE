import base64, hashlib, hmac, json, re, secrets, time


class AuthError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_encode(payload: dict, secret: str, ttl_s: int = 86400) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = {**payload, "exp": time.time() + ttl_s}
    h = _b64(json.dumps(header).encode())
    p = _b64(json.dumps(body).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64(sig)}"


def jwt_decode(token: str, secret: str) -> dict:
    try:
        h, p, s = token.split(".")
        expected = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(s)):
            raise AuthError("bad signature")
        payload = json.loads(_unb64(p))
        if payload.get("exp", 0) < time.time():
            raise AuthError("expired")
        return payload
    except AuthError:
        raise
    except Exception as e:
        raise AuthError(str(e)) from e


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"{salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$")
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
    except ValueError:
        return False
    return hmac.compare_digest(h.hex(), hash_hex)


def oib_valid(oib: str) -> bool:
    if not re.fullmatch(r"\d{11}", oib):
        return False
    a = 10
    for d in oib[:10]:
        a = (a + int(d)) % 10
        a = 10 if a == 0 else a
        a = (a * 2) % 11
    control = (11 - a) % 10
    return control == int(oib[10])


_IBAN_RE = re.compile(r"\bHR\d{19}\b")
_OIB_RE = re.compile(r"\b\d{11}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"(\+385|0)[0-9 /-]{7,}")


def redact_pii(text: str) -> str:
    text = _IBAN_RE.sub("[IBAN]", text)
    text = _OIB_RE.sub(lambda m: "[OIB]" if oib_valid(m.group()) else m.group(), text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[TEL]", text)
    return text


def chain_append(spine, event: str) -> str:
    with spine.write() as c:
        row = c.execute("SELECT hash FROM hash_chain ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = row["hash"] if row else ""
        h = hashlib.sha256((prev_hash + event).encode()).hexdigest()
        c.execute("INSERT INTO hash_chain(event, prev_hash, hash) VALUES(?,?,?)",
                  (event, prev_hash, h))
        return h


def chain_verify(spine) -> bool:
    rows = spine.read().execute("SELECT event, prev_hash, hash FROM hash_chain ORDER BY id ASC").fetchall()
    prev_hash = ""
    for r in rows:
        if r["prev_hash"] != prev_hash:
            return False
        h = hashlib.sha256((prev_hash + r["event"]).encode()).hexdigest()
        if h != r["hash"]:
            return False
        prev_hash = h
    return True
