"""Otkrivanje klijenata iz naziva podmapa KLIJENTI mape (bez OIB-a). Predloži pa
potvrdi; upsert clients po nas_folder-u (idempotentno). Ne dira datoteke na disku."""
import os
import re

from ragspine.business import folders

_COMPANY = re.compile(r"\b(d\.?o\.?o\.?|j\.?d\.?o\.?o\.?|d\.?d\.?|obrt)\b", re.IGNORECASE)


def _guess_type(name: str) -> str:
    return "company" if _COMPANY.search(name or "") else "person"


def _norm(name: str) -> str:
    """Normaliziran ključ za podudaranje: fold dijakritika + redoslijed riječi nebitan
    (prezime/ime vs ime/prezime)."""
    n = (name or "").lower()
    for a, b in zip("čćžšđ", "cczsd"):
        n = n.replace(a, b)
    return " ".join(sorted(re.findall(r"\w+", n)))


def discover(spine, cfg, folder_id: int) -> list[dict]:
    row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise ValueError("nepoznata mapa")
    base = folders._scoped(cfg, row["path"])
    existing = {_norm(r["name"]): r["id"] for r in
                spine.read().execute("SELECT id, name FROM clients").fetchall()}
    out = []
    for e in sorted(os.scandir(base), key=lambda x: x.name):
        if not e.is_dir():
            continue
        out.append({"subdir": e.name, "raw_name": e.name,
                    "guessed_type": _guess_type(e.name),
                    "match_id": existing.get(_norm(e.name))})
    return out


def commit(spine, cfg, folder_id: int, items: list[dict]) -> dict:
    row = spine.read().execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise ValueError("nepoznata mapa")
    base = folders._scoped(cfg, row["path"])
    created = merged = skipped = 0
    with spine.write() as c:
        for it in items:
            if it.get("action") == "skip":
                skipped += 1
                continue
            # apsolutni realpath podmape = stabilan, jedinstven identifikator veze mapa↔klijent
            nas = os.path.realpath(os.path.join(base, it["subdir"]))
            if it.get("action") == "merge" and it.get("merge_id"):
                c.execute("UPDATE clients SET nas_folder=? WHERE id=?", (nas, it["merge_id"]))
                merged += 1
                continue
            hit = c.execute("SELECT id FROM clients WHERE nas_folder=?", (nas,)).fetchone()
            if hit:
                c.execute("UPDATE clients SET name=? WHERE id=?", (it["name"], hit["id"]))
            else:
                c.execute("INSERT INTO clients(name, nas_folder) VALUES(?,?)", (it["name"], nas))
                created += 1
    return {"created": created, "merged": merged, "skipped": skipped}
