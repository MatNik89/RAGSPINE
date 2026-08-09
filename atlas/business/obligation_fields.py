"""User-defined polja obveza. Admin/owner definira "stupce" iz Postavki; core
stupci su ZAKLJUČANI (ne mogu se definirati kao custom). Vrijednosti žive u JSON
`meta` na obligations — nema DDL u letu (ni AI ni UI ne mijenjaju strukturu).
"""
import json
import math
import re
from datetime import date

# Rezervirani/core ključevi — ne smiju se definirati kao user-defined polje
CORE_KEYS = frozenset({
    "id", "obligation_id", "client_id", "client", "kind", "period",
    "sent", "sent_by", "sent_at", "meta",
})
TYPES = ("text", "date", "number", "bool")


def _norm_key(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("key mora biti string")
    k = key.strip().lower()
    for a, b in zip("čćžšđ", "cczsd"):
        k = k.replace(a, b)
    k = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
    if not k:
        raise ValueError("naziv polja je obavezan")
    return k


def add_field(spine, label: str, type: str = "text", user: str = "?", key: str | None = None) -> str:
    if type not in TYPES:
        raise ValueError(f"nepoznat tip polja: {type!r} (dozvoljeno: {', '.join(TYPES)})")
    key = _norm_key(key if key is not None else label)
    if key in CORE_KEYS:
        raise ValueError(f"'{key}' je zaključan core stupac — ne može biti user-defined")
    label = (label or "").strip() or key
    with spine.write() as c:
        c.execute(
            """INSERT INTO obligation_fields(key, label, type, created_by) VALUES(?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET label=excluded.label, type=excluded.type""",
            (key, label, type, user))
    spine.audit(user, "obligation_field_add", key, type)
    return key


def list_fields(spine) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        "SELECT key, label, type, sort FROM obligation_fields ORDER BY sort, key").fetchall()]


def remove_field(spine, key: str, user: str = "?") -> None:
    key = _norm_key(key)
    with spine.write() as c:
        c.execute("DELETE FROM obligation_fields WHERE key=?", (key,))
    spine.audit(user, "obligation_field_remove", key)


def _coerce(type: str, value):
    """Provjeri + pretvori vrijednost prema tipu polja. Baca ValueError."""
    if type == "number":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError("vrijednost mora biti broj") from None
        if not math.isfinite(f):  # NaN/Infinity bi otrovao red i srušio JSON odgovor
            raise ValueError("broj mora biti konačan")
        return f
    if type == "bool":
        if value in (True, 1, "1", "true", "True", "da"):
            return 1
        if value in (False, 0, "0", "false", "False", "ne", None):
            return 0
        raise ValueError("vrijednost mora biti da/ne")
    if type == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise ValueError("vrijednost mora biti datum (GGGG-MM-DD)") from None
    return str(value)


def get_meta(spine, obligation_id: int) -> dict:
    row = spine.read().execute("SELECT meta FROM obligations WHERE id=?", (obligation_id,)).fetchone()
    if row is None:
        raise ValueError(f"nepoznata obveza: {obligation_id}")
    try:
        return json.loads(row["meta"]) if row["meta"] else {}
    except (ValueError, TypeError):
        return {}


def set_value(spine, obligation_id: int, key: str, value, user: str = "?") -> dict:
    key = _norm_key(key)
    field = spine.read().execute(
        "SELECT type FROM obligation_fields WHERE key=?", (key,)).fetchone()
    if field is None:
        raise ValueError(f"polje '{key}' nije definirano (dodaj ga u Postavke → Obaveze)")
    coerced = _coerce(field["type"], value)
    with spine.write() as c:
        row = c.execute("SELECT meta FROM obligations WHERE id=?", (obligation_id,)).fetchone()
        if row is None:
            raise ValueError(f"nepoznata obveza: {obligation_id}")
        meta = {}
        if row["meta"]:
            try:
                meta = json.loads(row["meta"])
            except (ValueError, TypeError):
                meta = {}
        meta[key] = coerced
        c.execute("UPDATE obligations SET meta=? WHERE id=?",
                  (json.dumps(meta, ensure_ascii=False), obligation_id))
    spine.audit(user, "obligation_field_set", f"obligation:{obligation_id}", key)
    return meta
