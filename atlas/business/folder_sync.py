# Faza 2: sinkronizacija registriranih mrežnih mapa u bazu.
#
# Za mapu uloge 'propisi': datoteka u podmapi (Zakoni/Pravilnici/Uredbe/...) dobije
# doc_type = tier iz naziva podmape pa retrieval autoritet bude točan. Promijenjena
# datoteka (novi sadržaj) → nova aktivna verzija, stara → superseded (retrieval već
# filtrira status='active', pa stari propis ispadne iz odgovora).

import os

from atlas.business import folders as folders_mod
from atlas.docs import ingest as ingest_mod
from atlas.docs.ingest import UnsupportedFormat
from atlas.rag.authority import _fold

# naziv podmape (folded) → authority tier (ključ iz authority.AUTHORITY)
_TIER_BY_DIR = {
    "zakoni": "zakon", "zakon": "zakon",
    "pravilnici": "pravilnik", "pravilnik": "pravilnik",
    "uredbe": "uredba", "uredba": "uredba",
    "misljenja": "misljenje_porezna", "misljenje": "misljenje_porezna",
    "misljenja porezne": "misljenje_porezna",
    "nn": "nn_objava", "narodne novine": "nn_objava",
    "kolektivni ugovori": "kolektivni_ugovor", "kolektivni ugovor": "kolektivni_ugovor",
}


def _subfolder_tier(folder_path: str, file_path: str) -> str | None:
    """Tier iz prve podmape ispod registrirane mape (npr. .../Propisi/Pravilnici/x.pdf
    → 'pravilnik'). Datoteka direktno u korijenu (bez podmape) → None (auto-detekcija)."""
    rel = os.path.relpath(file_path, folder_path)
    parts = rel.split(os.sep)
    if len(parts) < 2:
        return None
    return _TIER_BY_DIR.get(_fold(parts[0]))


def _reconcile_path(spine, path: str) -> int:
    """Idempotentno: za jednu putanju ostavi SAMO najnoviji aktivni dokument
    (najveći id), starije aktivne → superseded, novom postavi verziju/pred, uz
    predecessor = najviši od starijih. Radi i kad je prethodni sync pao između
    inserta i supersede-a (crash-safe) — sljedeći poziv počisti."""
    with spine.write() as c:
        rows = c.execute(
            "SELECT id, version FROM documents WHERE path=? AND status='active' ORDER BY id",
            (path,),
        ).fetchall()
        if len(rows) <= 1:
            return 0
        keep, older = rows[-1], rows[:-1]  # najveći id = najnoviji
        maxv = max((r["version"] or 1) for r in rows)
        for r in older:
            c.execute("UPDATE documents SET status='superseded' WHERE id=?", (r["id"],))
        c.execute("UPDATE documents SET supersedes=?, version=? WHERE id=?",
                  (older[-1]["id"], maxv + 1, keep["id"]))
    return len(older)


def _active_file_sha(spine, path: str) -> str | None:
    r = spine.read().execute(
        "SELECT file_sha FROM documents WHERE path=? AND status='active' ORDER BY id DESC LIMIT 1",
        (path,),
    ).fetchone()
    return r["file_sha"] if r else None


def sync_folder(spine, cfg, folder: dict) -> dict:
    counts = {"ingested": 0, "skipped": 0, "superseded": 0, "errors": []}
    role = folder.get("role") or "ostalo"
    # Revalidiraj korijen SVAKI put (mount mogao pasti, root maknut iz mount_roots,
    # ili mapa zamijenjena simlinkom nakon registracije).
    try:
        path = folders_mod._scoped(cfg, folder["path"])
    except ValueError as e:
        counts["errors"].append(f"mapa izvan dozvoljenih korijena: {e}")
        return counts
    if not os.path.isdir(path) or os.path.islink(folder["path"]):
        counts["errors"].append(f"nedostupna mapa: {folder['path']}")
        return counts
    if role == "klijenti":
        return counts  # klijentske mape drži onboarding
    roots = cfg.mount_roots or []
    for root, dirs, files in os.walk(path, followlinks=False):
        # ne silazi u simlinkane podmape (mogu voditi van korijena)
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for fname in files:
            fp = os.path.join(root, fname)
            # preskoči simlink-datoteke i sve što realpath odvodi van korijena (TOCTOU/escape)
            if os.path.islink(fp) or not folders_mod._under_a_root(os.path.realpath(fp), roots):
                counts["skipped"] += 1
                continue
            dtype = _subfolder_tier(path, fp) if role == "propisi" else None
            try:
                fsha = ingest_mod._file_sha(fp)
                if _active_file_sha(spine, fp) == fsha:
                    counts["skipped"] += 1  # nepromijenjeno (po SADRŽAJU DATOTEKE)
                else:
                    doc_id = ingest_mod.ingest_file(spine, fp, doc_type=dtype)
                    counts["skipped" if doc_id is None else "ingested"] += 1
            except UnsupportedFormat:
                counts["skipped"] += 1
                continue
            except Exception as e:  # jedna loša datoteka ne ruši sinkronizaciju
                counts["errors"].append(f"{fp}: {e}")
                continue
            counts["superseded"] += _reconcile_path(spine, fp)  # uvijek, idempotentno
    return counts


def sync_all(spine, cfg) -> dict:
    total = {"folders": 0, "ingested": 0, "skipped": 0, "superseded": 0, "errors": []}
    for folder in folders_mod.list_folders(spine):
        if not folder.get("enabled"):
            continue
        total["folders"] += 1
        r = sync_folder(spine, cfg, folder)
        for k in ("ingested", "skipped", "superseded"):
            total[k] += r[k]
        total["errors"].extend(r["errors"])
        with spine.write() as c:
            c.execute("UPDATE folders SET last_synced=datetime('now') WHERE id=?", (folder["id"],))
    return total
