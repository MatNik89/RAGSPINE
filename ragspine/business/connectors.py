"""Connector framework za vanjske izvore (mail, kanali poruka). Uzor: Onyx
lifecycle + RAGFlow katalog. Ugovor: TIP/SHEMA → test(draft) PRIJE spremanja →
atomski validate+save → status/logs. Izolacija kvara po konektoru (jedan pukne,
ostali rade). OAuth/QR = status 'pending' dok autorizacija ne završi.

Adapteri se registriraju s poljima (shema za UI) i test funkcijom; stvarna
logika slanja/primanja dolazi u zasebnim adapterima. Ovaj modul je samo okvir."""
import json
from dataclasses import dataclass, field

# status: pending (OAuth/QR čeka), connected, error, disabled
STATUSES = ("pending", "connected", "error", "disabled")


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"        # text | password | select
    required: bool = True
    secret: bool = False       # maskira se pri prikazu
    options: list | None = None  # za select


@dataclass
class ConnectorType:
    kind: str
    label: str
    fields: list
    # test(config) -> (status, detail): status ∈ connected|pending|error
    test: object = None
    category: str = "kanal"    # 'mail' | 'kanal'


_TYPES: dict[str, ConnectorType] = {}


def register(ctype: ConnectorType) -> None:
    _TYPES[ctype.kind] = ctype


def get_type(kind: str) -> ConnectorType | None:
    return _TYPES.get(kind)


def list_types() -> list[dict]:
    """Katalog 'Dostupno' za UI."""
    return [{"kind": t.kind, "label": t.label, "category": t.category,
             "fields": [vars(f) for f in t.fields]} for t in _TYPES.values()]


def _validate_config(ctype: ConnectorType, config: dict) -> None:
    missing = [f.key for f in ctype.fields if f.required and not str(config.get(f.key, "")).strip()]
    if missing:
        raise ValueError(f"nedostaju obavezna polja: {', '.join(missing)}")


def test_draft(kind: str, config: dict) -> dict:
    """Testira konfiguraciju BEZ spremanja (Onyx: test draft prije save)."""
    ctype = get_type(kind)
    if ctype is None:
        raise ValueError(f"nepoznat tip konektora: {kind!r}")
    _validate_config(ctype, config)
    if ctype.test is None:
        return {"status": "error", "detail": "adapter nema test funkciju"}
    try:
        status, detail = ctype.test(config)
    except Exception as e:  # izolacija: greška adaptera ne ruši okvir
        return {"status": "error", "detail": f"greška testa: {e}"}
    return {"status": status, "detail": detail}


def _mask(ctype: ConnectorType, config: dict) -> dict:
    secret_keys = {f.key for f in ctype.fields if f.secret}
    return {k: ("••••" if k in secret_keys and v else v) for k, v in config.items()}


def _row(spine, r) -> dict:
    ctype = get_type(r["kind"])
    cfg = json.loads(r["config_json"] or "{}")
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "status": r["status"],
            "last_ok": r["last_ok"], "last_error": r["last_error"],
            "label": ctype.label if ctype else r["kind"],
            "config": _mask(ctype, cfg) if ctype else {}}


def list_connectors(spine) -> list[dict]:
    """'Konfigurirano' za UI — sa statusom, tajne maskirane."""
    rows = spine.read().execute("SELECT * FROM connectors ORDER BY id").fetchall()
    return [_row(spine, r) for r in rows]


def get(spine, cid: int) -> dict | None:
    r = spine.read().execute("SELECT * FROM connectors WHERE id=?", (cid,)).fetchone()
    return _row(spine, r) if r else None


def create(spine, kind: str, name: str, config: dict, user: str = "?") -> dict:
    """Testira PA sprema s dobivenim statusom (Onyx: nikad ne spremi neprovjereno).
    Test 'error' se svejedno sprema (status=error) da operater vidi i popravi."""
    ctype = get_type(kind)
    if ctype is None:
        raise ValueError(f"nepoznat tip konektora: {kind!r}")
    if not name.strip():
        raise ValueError("naziv je obavezan")
    _validate_config(ctype, config)
    res = test_draft(kind, config)
    ok = res["status"] == "connected"
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO connectors(kind, name, config_json, status, last_ok, last_error, created_by) "
            "VALUES(?,?,?,?,?,?,?)",
            (kind, name.strip(), json.dumps(config), res["status"],
             _now() if ok else None, None if ok else res["detail"], user))
        cid = cur.lastrowid
    spine.audit(user, "connector_create", f"{kind}:{name}:{res['status']}")
    return {"id": cid, **res}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def set_status(spine, cid: int, status: str, detail: str = "", user: str = "?") -> None:
    if status not in STATUSES:
        raise ValueError(f"nepoznat status: {status!r}")
    with spine.write() as c:
        if c.execute("SELECT 1 FROM connectors WHERE id=?", (cid,)).fetchone() is None:
            raise ValueError(f"nepoznat konektor: {cid}")
        c.execute("UPDATE connectors SET status=?, last_error=? WHERE id=?",
                  (status, detail or None, cid))
    spine.audit(user, "connector_status", f"{cid}:{status}")


def delete(spine, cid: int, user: str = "?") -> None:
    with spine.write() as c:
        c.execute("DELETE FROM connectors WHERE id=?", (cid,))
    spine.audit(user, "connector_delete", str(cid))
