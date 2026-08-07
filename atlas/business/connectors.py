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


def _clean_config(ctype: ConnectorType, config: dict) -> dict:
    """Zadrži SAMO deklarirana polja, svako kao string (Codex: config dopuštao
    liste/dictove/nepoznate ključeve). Provjeri obavezna."""
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
    """Vrati SAMO deklarirana polja (Codex: nepoznati/ugniježđeni ključevi su
    prije prolazili neredigirano); tajne → ••••."""
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
    """'Konfigurirano' za UI — sa statusom, tajne maskirane, org-scopeano."""
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
    """(kind, dešifrirani config) za server-side adapter (spajanje). NIKAD se ne
    izlaže rutom — tajne su ovdje u plaintextu samo u memoriji."""
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
    """Testira PA sprema s dobivenim statusom (Onyx: nikad ne spremi neprovjereno).
    Tajne se ŠIFRIRAJU prije spremanja (secretbox, ključ iz jwt_secreta izvan DB-a)."""
    from atlas.business import secretbox
    ctype = get_type(kind)
    if ctype is None:
        raise ValueError(f"nepoznat tip konektora: {kind!r}")
    if not name.strip():
        raise ValueError("naziv je obavezan")
    clean = _clean_config(ctype, config)
    res = test_draft(kind, clean)
    ok = res["status"] == "connected"
    # šifriraj tajna polja prije spremanja
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


# admin smije samo uključiti/isključiti; connected/error postavlja adapter (Codex)
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
