# Karton klijenta (Client-360) — jedan agregatni fetch za GET /clients/{id}/karton.json.
# Svaka sekcija je best-effort: pad jedne (npr. checklist baci iznimku) ne
# smije srušiti cijeli karton — degradira na prazan/default rezultat.
from datetime import date

from ragspine.business import checklist, cjenik, notes, obveze
from ragspine.business import onboarding


def _urgency(expires: str, today: date) -> tuple:
    try:
        delta = (date.fromisoformat(expires) - today).days
    except (TypeError, ValueError):
        return None, "ok"
    if delta < 0:
        return delta, "bad"
    if delta <= 7:
        return delta, "warn"
    return delta, "ok"


def _client_obligations(spine, client_id: int, period: str) -> list[dict]:
    for kind in obveze.KINDS:
        obveze.ensure_period(spine, kind, period)
    rows = spine.read().execute(
        """SELECT o.kind AS kind, o.period AS period, COALESCE(s.sent, 0) AS sent,
                  s.sent_at AS sent_at
           FROM obligations o
           LEFT JOIN obligation_status s ON s.obligation_id = o.id
           WHERE o.client_id = ? AND o.period = ?
           ORDER BY o.kind""",
        (client_id, period),
    ).fetchall()
    return [dict(r) for r in rows]


def _client_expiry(spine, client_id: int) -> list[dict]:
    # ponytail: unwindowed (all tracked items, not just "expiring soon") — the
    # karton is a per-client dossier, not a triage queue like /dashboard.json.
    today = date.today()
    rows = spine.read().execute(
        "SELECT id, kind, label, expires FROM expiry_items WHERE client_id=? ORDER BY expires",
        (client_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["days_left"], d["state"] = _urgency(d["expires"], today)
        out.append(d)
    return out


def _client_eracuni(spine, oib: str | None) -> dict:
    if not oib:
        return {"count": 0, "recent": []}
    count = spine.read().execute(
        "SELECT COUNT(*) AS n FROM eracuni WHERE supplier_oib=? OR customer_oib=?", (oib, oib)
    ).fetchone()["n"]
    recent = spine.read().execute(
        """SELECT id, supplier_oib, customer_oib, total, vat, currency, issued
           FROM eracuni WHERE supplier_oib=? OR customer_oib=?
           ORDER BY issued DESC LIMIT 10""",
        (oib, oib),
    ).fetchall()
    return {"count": count, "recent": [dict(r) for r in recent]}


def karton_data(spine, cfg, client_id: int) -> dict:
    row = spine.read().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if row is None:
        raise ValueError(f"nepoznat klijent: {client_id}")
    client = dict(row)
    period = date.today().strftime("%Y-%m")

    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    checklist_data = _safe(lambda: checklist.score_client(spine, client_id),
                            {"score": 0, "missing": []})
    notes_rows = _safe(lambda: [dict(r) for r in notes.search(spine, client_id=client_id)], [])
    sops = _safe(lambda: [dict(r) for r in spine.read().execute(
        "SELECT id, title, category FROM sop_pages WHERE client_id=? AND status='approved' "
        "ORDER BY updated_at DESC", (client_id,)).fetchall()], [])
    obligations = _safe(lambda: _client_obligations(spine, client_id, period), [])
    expiry_rows = _safe(lambda: _client_expiry(spine, client_id), [])
    cjenik_data = _safe(lambda: {
        "ukupno": cjenik.izracunaj_cijenu(spine, client_id)["ukupno"],
        "usporedba": cjenik.usporedi_s_trzistem(spine, client_id),
    }, {"ukupno": 0, "usporedba": None})
    eracuni = _safe(lambda: _client_eracuni(spine, client.get("oib")), {"count": 0, "recent": []})
    documents = _safe(lambda: onboarding.list_documents(spine, cfg, client_id), [])

    return {
        "client": client,
        "checklist": checklist_data,
        "notes": notes_rows,
        "sops": sops,
        "obligations": obligations,
        "expiry": expiry_rows,
        "cjenik": cjenik_data,
        "eracuni": eracuni,
        "documents": documents,
    }
