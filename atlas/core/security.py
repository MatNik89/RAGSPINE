import base64, hashlib, hmac, json, os, re, secrets, time


class AuthError(Exception):
    pass


def path_under(path: str, root: str) -> bool:
    """True if path lies at/under root (both already realpath-ed by the caller).
    Fail-closed: a Windows drive-mismatch (commonpath ValueError) = outside root."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


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


_PBKDF2_ITERS = 600_000


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2${_PBKDF2_ITERS}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored:  # NULL/empty = "awaiting activation" — never passes verify
        return False
    try:
        if stored.startswith("pbkdf2$"):
            _, iters_s, salt_hex, hash_hex = stored.split("$")
            iters = int(iters_s)
        else:
            salt_hex, hash_hex = stored.split("$")   # legacy 200k
            iters = 200_000
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), iters)
    except ValueError:
        return False
    return secrets.compare_digest(h.hex(), hash_hex)


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


_SECRET_KEYS = ("token", "secret", "password", "passwd", "pwd", "lozinka",
                "api_key", "apikey", "sign_key", "signkey", "access_token",
                "refresh_token", "bot_token", "client_secret", "authorization",
                "private_key", "credentials")
_KEYS_ALT = "|".join(_SECRET_KEYS)
# QUOTED: "key": "value with spaces and } inside" -> the value is everything up to the closing quote
_SECRET_QUOTED_RE = re.compile(r'("?(?:' + _KEYS_ALT + r')"?\s*[:=]\s*")([^"]*)(")', re.IGNORECASE)
# BARE: key=value or key: value (unquoted) -> up to a space/comma/brace
_SECRET_BARE_RE = re.compile(r'((?:' + _KEYS_ALT + r')"?\s*[:=]\s*)([^"\s,}&]+)', re.IGNORECASE)
# password in a URL: scheme://user:PASS@host
_URL_CRED_RE = re.compile(r"(://[^/\s:@]+:)([^/\s@]+)(@)")


def redact_secrets(text: str) -> str:
    """Obscure secrets before writing to the audit/log (token/secret/password/sign_key/
    authorization/... and a password in a URL). Defensive: `agent_execute` logs
    arbitrary tool args — a future tool with a secret field must not leak into the
    plaintext audit. Quoted values tolerate spaces and braces inside the quotes."""
    if not text:
        return text
    text = _SECRET_QUOTED_RE.sub(r"\1[REDACTED]\3", text)
    text = _SECRET_BARE_RE.sub(r"\1[REDACTED]", text)
    text = _URL_CRED_RE.sub(r"\1[REDACTED]\3", text)
    return text


def redact_secret_values(text: str, values) -> str:
    """Remove the EXACT secret values from the text (e.g. a token a tool returns in
    an error string) — complement to redact_secrets (which works by field NAME).
    Longest-first so a longer value is not left partially visible when a shorter
    one is its prefix (Paperclip). Threshold 6 chars (shorter would falsely mask
    ordinary words)."""
    if not text:
        return text
    for v in sorted({str(x) for x in values if x and len(str(x)) >= 6}, key=len, reverse=True):
        text = text.replace(v, "[REDACTED]")
    return text


def chain_append(spine, event: str) -> str:
    # BEGIN IMMEDIATE takes the SQLite write lock up front, so the
    # read-last-hash + insert is atomic across processes (not just
    # in-process via spine's threading.Lock) — otherwise a concurrent
    # CLI command and a live serve process can race and fork the chain.
    with spine.write() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT hash FROM hash_chain ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = row["hash"] if row else ""
            h = hashlib.sha256((prev_hash + event).encode()).hexdigest()
            c.execute("INSERT INTO hash_chain(event, prev_hash, hash) VALUES(?,?,?)",
                      (event, prev_hash, h))
        except Exception:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")
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
