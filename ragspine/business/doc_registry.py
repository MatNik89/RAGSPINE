# Registar vrsta dokumenata (doc_types) — data-driven kao obligation_types.
#
# Svaka vrsta nosi polja za ekstrakciju (C3 regex+LLM): [{key,label,kind,expiry}].
# kind ∈ text|date; expiry=True smije samo na date polju — taj datum ide u
# rok-alert (7 dana prije isteka). Brisanja nema, deaktivacija preko active=0.

import json
import re

FIELD_KINDS = ("text", "date")

# (key, label, fields, sort) — seed lijeno INSERT OR IGNORE, admin-izmjene ostaju.
DEFAULT_TYPES = [
    ("osobna_iskaznica", "Osobna iskaznica", [
        {"key": "broj", "label": "Broj", "kind": "text", "expiry": False},
        {"key": "datum_izdavanja", "label": "Datum izdavanja", "kind": "date", "expiry": False},
        {"key": "mjesto_izdavanja", "label": "Mjesto izdavanja", "kind": "text", "expiry": False},
        {"key": "datum_isteka", "label": "Datum isteka", "kind": "date", "expiry": True},
    ], 10),
]


def _norm_key(key: str) -> str:
    """snake_case ključ: mala slova, dijakritika foldana, ostalo -> _."""
    if not isinstance(key, str):
        raise ValueError(f"key mora biti string, ne {type(key).__name__}")
    k = key.strip().lower()
    for a, b in zip("čćžšđ", "cczsd"):
        k = k.replace(a, b)
    k = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
    if not k:
        raise ValueError("key je obavezan")
    return k


def _validate_fields(fields) -> list[dict]:
    if not isinstance(fields, list):
        raise ValueError("fields mora biti lista")
    out, seen = [], set()
    for f in fields:
        if not isinstance(f, dict):
            raise ValueError("polje mora biti objekt")
        fk = _norm_key(f.get("key", ""))
        if fk in seen:
            raise ValueError(f"duplicirano polje: {fk!r}")
        seen.add(fk)
        kind = f.get("kind") or "text"
        if kind not in FIELD_KINDS:
            raise ValueError(f"nepoznat kind polja: {kind!r} (dozvoljeno: {FIELD_KINDS})")
        expiry = bool(f.get("expiry"))
        if expiry and kind != "date":
            raise ValueError(f"expiry smije samo na date polje: {fk!r}")
        label = f.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError(f"label mora biti string: {fk!r}")
        out.append({"key": fk, "label": (label or fk).strip(),
                    "kind": kind, "expiry": expiry})
    return out


def _ensure_seeded(spine) -> None:
    with spine.write() as c:
        for key, label, fields, sort in DEFAULT_TYPES:
            c.execute(
                "INSERT OR IGNORE INTO doc_types(key, label, fields_json, sort) VALUES(?,?,?,?)",
                (key, label, json.dumps(fields, ensure_ascii=False), sort),
            )


def list_types(spine, active_only: bool = False) -> list[dict]:
    _ensure_seeded(spine)
    q = "SELECT key, label, fields_json, active, sort FROM doc_types"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY sort, key"
    out = []
    for r in spine.read().execute(q).fetchall():
        d = dict(r)
        try:
            d["fields"] = json.loads(d.pop("fields_json") or "[]")
        except ValueError:
            d["fields"] = []
        out.append(d)
    return out


def upsert(spine, key: str, label: str, fields, active: bool = True,
           sort: int = 100, user: str = "?") -> str:
    """Kreira ili uređuje vrstu dokumenta. Vraća normalizirani key."""
    key = _norm_key(key)
    fields = _validate_fields(fields)
    _ensure_seeded(spine)
    with spine.write() as c:
        c.execute(
            """INSERT INTO doc_types(key, label, fields_json, active, sort)
               VALUES(?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET label=excluded.label,
                 fields_json=excluded.fields_json, active=excluded.active,
                 sort=excluded.sort""",
            (key, (label or key).strip(), json.dumps(fields, ensure_ascii=False),
             1 if active else 0, int(sort)),
        )
    spine.audit(user, "doc_type_upsert", f"doc_type:{key}")
    return key


def export_json(spine) -> dict:
    """Cijeli registar kao JSON — backup / dijeljenje između ureda."""
    return {"version": 1, "doc_types": list_types(spine)}
