"""Hibridna ekstrakcija polja iz teksta dokumenta (C3): regex prvo, LLM samo za
polja koja regex nije našao. Polja definira doc_types registar (C2); expiry
polje s valjanim datumom ide u expiry_items (rok-alert na dashboardu)."""

import datetime
import json
import logging
import re

from ragspine.business import doc_registry

_log = logging.getLogger(__name__)

# 15.08.2026 / 15. 8. 2026. / 2026-08-15
_DATE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")


def _iso_date(m: re.Match) -> str | None:
    try:
        if m.group(4):  # ISO grana
            d, mo, y = int(m.group(6)), int(m.group(5)), int(m.group(4))
        else:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime.date(y, mo, d).isoformat()  # odbija 31.02., 29.02. ne-prijestupne...
    except (ValueError, TypeError):
        return None


def _text_dates(text: str) -> set[str]:
    return {iso for m in _DATE.finditer(text or "") if (iso := _iso_date(m))}


def _label_variants(field: dict) -> list[str]:
    out = [field.get("label") or "", field["key"].replace("_", " ")]
    return [v for v in dict.fromkeys(v.strip() for v in out) if v]


def extract_regex(text: str, fields: list[dict]) -> dict:
    """{key: vrijednost} za polja nađena labelom u istom retku; date
    normaliziran u ISO. Nenađeno polje se ne pojavljuje u rezultatu."""
    out = {}
    for f in fields:
        for label in _label_variants(f):
            # labela mora biti POČETAK retka + eksplicitni delimiter — inače
            # "Kontrolni broj: 1" pogodi polje "broj", a "Broj računa: 42"
            # vrati "računa: 42" (Codex nalaz, kolizije labela)
            pat = re.compile(rf"(?im)^\s*{re.escape(label)}\s*[:.]\s*(.+)$")
            m = pat.search(text or "")
            if not m:
                continue
            rest = m.group(1).strip()
            if f["kind"] == "date":
                dm = _DATE.search(rest)
                if dm and (iso := _iso_date(dm)):
                    out[f["key"]] = iso
                    break
            else:
                # vrijednost = ostatak retka do prvog dvostrukog razmaka/tab
                val = re.split(r"\s{2,}|\t", rest)[0].strip().rstrip(",;")
                if val:
                    out[f["key"]] = val
                    break
    return out


def extract_llm(llm, text: str, fields: list[dict]) -> dict:
    """Jedan LLM poziv za zadana polja; JSON objekt {key: value|null}, datumi
    YYYY-MM-DD. Greška/nedostupan LLM ili neispravan JSON -> {} (degradacija)."""
    if llm is None or not fields:
        return {}
    spec = "\n".join(
        f'- "{f["key"]}": {f.get("label") or f["key"]}'
        f' ({"datum, format YYYY-MM-DD" if f["kind"] == "date" else "tekst"})'
        for f in fields)
    system = (
        "Iz danog teksta dokumenta izvuci tražena polja. Vrati ISKLJUČIVO JSON "
        "objekt s točno ovim ključevima (null ako polja nema u tekstu):\n" + spec)
    try:
        raw = llm.complete([{"role": "user", "content": (text or "")[:12000]}],
                           system=system).text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        data = json.loads(raw)
    except Exception as e:  # LLMUnavailable/LLMError/JSON — nikad ne ruši ekstrakciju
        _log.warning("LLM ekstrakcija preskočena: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    # grounding: LLM vrijednost se prihvaća SAMO ako postoji u tekstu dokumenta
    # (datum: ISO mora biti među datumima iz teksta; tekst: substring, case-fold)
    # — dokument ne može prompt-injectati izmišljene vrijednosti u registar.
    # ponytail: substring grounding pada na hrvatskoj fleksiji ("Rijeka" vs
    # "u Rijeci") — polja osobnih dokumenata su doslovne vrijednosti pa je OK;
    # upgrade path: fuzzy/lemma usporedba.
    dates_in_text = _text_dates(text)
    out = {}
    for f in fields:
        v = data.get(f["key"])
        if not isinstance(v, str) or not v.strip():
            continue
        v = v.strip()
        if f["kind"] == "date":
            dm = _DATE.search(v)
            iso = _iso_date(dm) if dm else None
            if iso and iso in dates_in_text:
                out[f["key"]] = iso
        elif v.lower() in (text or "").lower():
            out[f["key"]] = v
    return out


def _doc_text(spine, doc_id: int) -> str:
    rows = spine.read().execute(
        "SELECT text FROM chunks WHERE doc_id=? ORDER BY seq", (doc_id,)).fetchall()
    return "\n".join(r["text"] or "" for r in rows)


def _upsert_expiry(spine, client_id: int, kind: str, label: str, expires: str) -> bool:
    """Upsert po (client_id, kind, label) — re-ekstrakcija ažurira, ne duplicira.
    Vraća True ako je red kreiran/ažuriran na novi datum."""
    with spine.write() as c:
        row = c.execute(
            "SELECT id, expires FROM expiry_items WHERE client_id=? AND kind=? AND label=?",
            (client_id, kind, label)).fetchone()
        if row is None:
            c.execute("INSERT INTO expiry_items(client_id, kind, label, expires) VALUES(?,?,?,?)",
                      (client_id, kind, label, expires))
            return True
        if row["expires"] != expires:
            c.execute("UPDATE expiry_items SET expires=? WHERE id=?", (expires, row["id"]))
            return True
    return False


def extract(spine, cfg, doc_id: int, doc_type_key: str, llm=None,
            client_id=None, user: str = "?") -> dict:
    doc = spine.read().execute(
        "SELECT id, title, client_id FROM documents WHERE id=?", (doc_id,)).fetchone()
    if doc is None:
        raise ValueError(f"nepoznat dokument: {doc_id}")
    dtype = next((t for t in doc_registry.list_types(spine) if t["key"] == doc_type_key), None)
    if dtype is None:
        raise ValueError(f"nepoznata vrsta dokumenta: {doc_type_key!r}")
    if client_id is None:
        client_id = doc["client_id"]

    text = _doc_text(spine, doc_id)
    fields = dtype["fields"]
    values = extract_regex(text, fields)
    engines = {k: "regex" for k in values}
    missing = [f for f in fields if f["key"] not in values]
    for k, v in extract_llm(llm, text, missing).items():
        values[k] = v
        engines[k] = "llm"

    with spine.write() as c:
        c.execute(
            """INSERT INTO doc_extracts(doc_id, doc_type_key, client_id, fields_json, engines_json)
               VALUES(?,?,?,?,?)
               ON CONFLICT(doc_id) DO UPDATE SET doc_type_key=excluded.doc_type_key,
                 client_id=excluded.client_id, fields_json=excluded.fields_json,
                 engines_json=excluded.engines_json, at=datetime('now')""",
            (doc_id, doc_type_key, client_id,
             json.dumps(values, ensure_ascii=False), json.dumps(engines)))

    expiry_created = 0
    for f in fields:
        if f.get("expiry") and f["key"] in values and client_id is not None:
            label = f'{dtype["label"]}: {f.get("label") or f["key"]}'
            if _upsert_expiry(spine, client_id, doc_type_key, label, values[f["key"]]):
                expiry_created += 1

    spine.audit(user, "doc_extract", f"doc:{doc_id}", doc_type_key)
    return {"doc_id": doc_id, "doc_type": doc_type_key, "client_id": client_id,
            "fields": values, "engines": engines, "expiry_created": expiry_created}
