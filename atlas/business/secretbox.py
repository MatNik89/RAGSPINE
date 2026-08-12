"""Encrypt connector secrets (passwords, tokens) before storing them in the DB.
The key is DERIVED from ATLAS_JWT_SECRET which lives in a file
(data_dir/secret, 0600) — NOT in the database or in the backup. That way a
stolen backup does not reveal mail passwords (Codex HIGH: plaintext secrets in
the DB also end up in a downloadable backup).

Fernet (AES-128-CBC + HMAC). ponytail: a key derived from jwt_secret is simple
and needs no new keystore; upgrade path = Windows DPAPI / external KMS."""
import base64
import hashlib

_PREFIX = "enc:"


def _key(cfg) -> bytes:
    secret = getattr(cfg, "jwt_secret", "") or ""
    # 32-byte Fernet key from sha256(jwt_secret)
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def available() -> bool:
    try:
        import cryptography.fernet  # noqa: F401
        return True
    except Exception:
        return False


def encrypt(value: str, cfg) -> str:
    """Return 'enc:<token>'. If cryptography is unavailable, raise — secrets are
    NOT stored in plaintext."""
    from cryptography.fernet import Fernet
    if not value:
        return value
    token = Fernet(_key(cfg)).encrypt(value.encode()).decode()
    return _PREFIX + token


def decrypt(value: str, cfg) -> str:
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return value  # not encrypted (old record / plaintext) — return as-is
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_key(cfg)).decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""  # wrong key (e.g. changed jwt_secret) — treat as empty


def key_fingerprint(cfg) -> str:
    """First 12 hex of sha256(jwt_secret) — diagnostics for a wrong key WITHOUT
    revealing the key (Paperclip). When a restore with the wrong key silently
    returns empty secrets, comparing the fingerprint shows whether the key is the
    same. '' if there is no secret. (The key file's permission check is already
    in doctor._check_secret_perms — we do not duplicate it.)"""
    secret = getattr(cfg, "jwt_secret", "") or ""
    return hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else ""
