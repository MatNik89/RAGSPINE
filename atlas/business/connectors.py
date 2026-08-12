"""Connector framework for external sources (mail, message channels). Model: Onyx
lifecycle + RAGFlow catalog. Contract: TYPE/SCHEMA -> test(draft) BEFORE saving ->
atomic validate+save -> status/logs. Fault isolation per connector (one breaks,
the others keep working). OAuth/QR = status 'pending' until authorization completes.

Adapters register with fields (schema for the UI) and a test function; the actual
send/receive logic lives in separate adapters. This module is only the framework."""
import json
from dataclasses import dataclass, field

# status: pending (OAuth/QR waiting), connected, error, disabled
STATUSES = ("pending", "connected", "error", "disabled")


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"        # text | password | select
    required: bool = True
    secret: bool = False       # masked when displayed
    options: list | None = None  # for select


@dataclass
class ConnectorType:
    kind: str
    label: str
    fields: list
    # test(config) -> (status, detail): status in connected|pending|error
    test: object = None
    category: str = "kanal"    # 'mail' | 'kanal'


_TYPES: dict[str, ConnectorType] = {}


def register(ctype: ConnectorType) -> None:
    _TYPES[ctype.kind] = ctype


def get_type(kind: str) -> ConnectorType | None:
    return _TYPES.get(kind)


def list_types() -> list[dict]:
    """'Available' catalog for the UI."""
    return [{"kind": t.kind, "label": t.label, "category": t.category,
             "fields": [vars(f) for f in t.fields]} for t in _TYPES.values()]


def _clean_config(ctype: ConnectorType, config: dict) -> dict:
    """Keep ONLY the declared fields, each as a string (Codex: config allowed
    lists/dicts/unknown keys). Check the required ones."""
    if not isinstance(config, dict):
        raise ValueError("config mora biti objekt")
    keys = {f.key for f in ctype.fields}
    clean = {}
    for f in ctype.fields:
        v = config.get(f.key, "")
        if isinstance(v, (dict, list)):
            raise ValueError(f"polje {f.key} mora biti tekst")
        clean[f.key] = "" if v is None else str(v)
    missing = [f.key for f in ctype.fields if f.required and not clean[f.key].strip()]
    if missing:
        raise ValueError(f"nedostaju obavezna polja: {', '.join(missing)}")
    return clean


def _validate_config(ctype: ConnectorType, config: dict) -> None:
    _clean_config(ctype, config)


def test_draft(kind: str, config: dict) -> dict:
    """Test the configuration WITHOUT saving (Onyx: test draft before save)."""
    ctype = get_type(kind)
    if ctype is None:
        raise ValueError(f"nepoznat tip konektora: {kind!r}")
    _validate_config(ctype, config)
    if ctype.test is None:
        return {"status": "error", "detail": "adapter nema test funkciju"}
    try:
        status, detail = ctype.test(config)
    except Exception as e:  # isolation: an adapter error does not crash the framework
        return {"status": "error", "detail": f"greška testa: {e}"}
    return {"status": status, "detail": detail}


def _mask(ctype: ConnectorType, config: dict) -> dict:
    """Return ONLY the declared fields (Codex: unknown/nested keys used to
    pass through unredacted); secrets -> ••••."""
    out = {}
    for f in ctype.fields:
        v = config.get(f.key, "")
        out[f.key] = "••••" if (f.secret and v) else v
    return out


def _row(spine, r) -> dict:
    ctype = get_type(r["kind"])
    cfg = json.loads(r["config_json"] or "{}")
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "status": r["status"],
            "last_ok": r["last_ok"], "last_error": r["last_error"],
            "label": ctype.label if ctype else r["kind"],
            "config": _mask(ctype, cfg) if ctype else {}}


def list_connectors(spine, org_id=None) -> list[dict]:
    """'Configured' list for the UI - with status, secrets masked, org-scoped."""
    if org_id is None:
        rows = spine.read().execute("SELECT * FROM connectors ORDER BY id").fetchall()
    else:
        rows = spine.read().execute(
            "SELECT * FROM connectors WHERE org_id=? ORDER BY id", (org_id,)).fetchall()
    return [_row(spine, r) for r in rows]


def get(spine, cid: int, org_id=None) -> dict | None:
    r = _get_row(spine, cid, org_id)
    return _row(spine, r) if r else None


def _get_row(spine, cid: int, org_id=None):
    if org_id is None:
        return spine.read().execute("SELECT * FROM connectors WHERE id=?", (cid,)).fetchone()
    return spine.read().execute(
        "SELECT * FROM connectors WHERE id=? AND org_id=?", (cid, org_id)).fetchone()


def config_for_adapter(spine, cid: int, cfg, org_id=None) -> tuple[str, dict] | None:
    """(kind, decrypted config) for the server-side adapter (connecting). NEVER
    exposed via a route - secrets are here in plaintext only in memory."""
    from atlas.business import secretbox
    r = _get_row(spine, cid, org_id)
    if r is None:
        return None
    ctype = get_type(r["kind"])
    stored = json.loads(r["config_json"] or "{}")
    out = {}
    for f in (ctype.fields if ctype else []):
        v = stored.get(f.key, "")
        out[f.key] = secretbox.decrypt(v, cfg) if f.secret else v
    return r["kind"], out


def create(spine, kind: str, name: str, config: dict, cfg=None, org_id=None, user: str = "?") -> dict:
    """Test THEN save with the resulting status (Onyx: never save unverified).
    Secrets are ENCRYPTED before saving (secretbox, key from jwt_secret outside the DB)."""
    from atlas.business import secretbox
    ctype = get_type(kind)
    if ctype is None:
        raise ValueError(f"nepoznat tip konektora: {kind!r}")
    if not name.strip():
        raise ValueError("naziv je obavezan")
    clean = _clean_config(ctype, config)
    res = test_draft(kind, clean)
    ok = res["status"] == "connected"
    # encrypt secret fields before saving
    to_store = dict(clean)
    for f in ctype.fields:
        if f.secret and clean[f.key]:
            if not secretbox.available():
                raise ValueError("šifriranje tajni nije dostupno (instaliraj cryptography)")
            to_store[f.key] = secretbox.encrypt(clean[f.key], cfg)
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO connectors(org_id, kind, name, config_json, status, last_ok, last_error, created_by) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (org_id, kind, name.strip(), json.dumps(to_store), res["status"],
             _now() if ok else None, None if ok else res["detail"], user))
        cid = cur.lastrowid
    spine.audit(user, "connector_create", f"{kind}:{name}:{res['status']}")
    return {"id": cid, **res}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# admin may only enable/disable; connected/error is set by the adapter (Codex)
_ADMIN_STATUSES = ("disabled",)


def set_status(spine, cid: int, status: str, org_id=None, user: str = "?") -> None:
    if status not in _ADMIN_STATUSES:
        raise ValueError(f"nedozvoljen prijelaz: {status!r} (admin smije samo isključiti)")
    with spine.write() as c:
        r = _get_row(spine, cid, org_id)
        if r is None:
            raise ValueError(f"nepoznat konektor: {cid}")
        c.execute("UPDATE connectors SET status=? WHERE id=?", (status, cid))
    spine.audit(user, "connector_status", f"{cid}:{status}")


def delete(spine, cid: int, org_id=None, user: str = "?") -> None:
    with spine.write() as c:
        if org_id is None:
            c.execute("DELETE FROM connectors WHERE id=?", (cid,))
        else:
            c.execute("DELETE FROM connectors WHERE id=? AND org_id=?", (cid, org_id))
    spine.audit(user, "connector_delete", str(cid))
