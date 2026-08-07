"""Šifriranje tajni konektora (lozinke, tokeni) prije spremanja u DB. Ključ se
IZVODI iz ATLAS_JWT_SECRET-a koji živi u datoteci (data_dir/secret, 0600) —
NIJE u bazi ni u backupu. Zato ukradeni backup ne otkriva mail lozinke (Codex
HIGH: plaintext tajne u DB-u završe i u preuzimljivom backupu).

Fernet (AES-128-CBC + HMAC). ponytail: ključ iz jwt_secreta je jednostavno i
bez novog keystore-a; upgrade path = Windows DPAPI / vanjski KMS."""
import base64
import hashlib

_PREFIX = "enc:"


def _key(cfg) -> bytes:
    secret = getattr(cfg, "jwt_secret", "") or ""
    # 32-bajtni Fernet ključ iz sha256(jwt_secret)
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def available() -> bool:
    try:
        import cryptography.fernet  # noqa: F401
        return True
    except Exception:
        return False


def encrypt(value: str, cfg) -> str:
    """Vrati 'enc:<token>'. Ako cryptography nedostupan, baci — tajne se NE
    spremaju u plaintext."""
    from cryptography.fernet import Fernet
    if not value:
        return value
    token = Fernet(_key(cfg)).encrypt(value.encode()).decode()
    return _PREFIX + token


def decrypt(value: str, cfg) -> str:
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return value  # nije šifrirano (stari zapis / plaintext) — vrati kako je
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_key(cfg)).decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""  # kriv ključ (npr. promijenjen jwt_secret) — tretiraj kao prazno
