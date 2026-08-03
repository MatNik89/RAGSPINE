# Faza 2: sinkronizacija registriranih mrežnih mapa u bazu.
#
# Za mapu uloge 'propisi': datoteka u podmapi (Zakoni/Pravilnici/Uredbe/...) dobije
# doc_type = tier iz naziva podmape pa retrieval autoritet bude točan. Promijenjena
# datoteka (novi sadržaj) → nova aktivna verzija, stara → superseded (retrieval već
# filtrira status='active', pa stari propis ispadne iz odgovora).

import os

from ragspine.business import folders as folders_mod
from ragspine.docs import ingest as ingest_mod
from ragspine.docs.ingest import UnsupportedFormat
from ragspine.rag.authority import _fold

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


def _supersede_prior(spine, path: str, new_id: int) -> int:
    """Stariji aktivni dokumenti iste putanje → superseded; novi → sljedeća verzija."""
    with spine.write() as c:
        prior = c.execute(
            "SELECT id, version FROM documents WHERE path=? AND status='active' AND id!=?",
            (path, new_id),
        ).fetchall()
        if not prior:
            return 0
        maxv = max((r["version"] or 1) for r in prior)
        for r in prior:
            c.execute("UPDATE documents SET status='superseded' WHERE id=?", (r["id"],))
        c.execute("UPDATE documents SET supersedes=?, version=? WHERE id=?",
                  (prior[0]["id"], maxv + 1, new_id))
    return len(prior)


def sync_folder(spine, folder: dict) -> dict:
    counts = {"ingested": 0, "skipped": 0, "superseded": 0, "errors": []}
    path = folder["path"]
    role = folder.get("role") or "ostalo"
    if not os.path.isdir(path):
        counts["errors"].append(f"nedostupna mapa: {path}")  # mount pao → preskoči, ne padaj
        return counts
    if role == "klijenti":
        # klijentske mape drži onboarding; ovdje ih ne ingestamo automatski
        return counts
    for root, _, files in os.walk(path):
        for fname in files:
            fp = os.path.join(root, fname)
            dtype = _subfolder_tier(path, fp) if role == "propisi" else None
            try:
                doc_id = ingest_mod.ingest_file(spine, fp, doc_type=dtype)
            except UnsupportedFormat:
                counts["skipped"] += 1
                continue
            except Exception as e:  # jedna loša datoteka ne ruši sinkronizaciju
                counts["errors"].append(f"{fp}: {e}")
                continue
            if doc_id is None:
                counts["skipped"] += 1  # nepromijenjeno (sha dedup)
                continue
            counts["ingested"] += 1
            counts["superseded"] += _supersede_prior(spine, fp, doc_id)
    return counts


def sync_all(spine, cfg) -> dict:
    total = {"folders": 0, "ingested": 0, "skipped": 0, "superseded": 0, "errors": []}
    for folder in folders_mod.list_folders(spine):
        if not folder.get("enabled"):
            continue
        total["folders"] += 1
        r = sync_folder(spine, folder)
        for k in ("ingested", "skipped", "superseded"):
            total[k] += r[k]
        total["errors"].extend(r["errors"])
        with spine.write() as c:
            c.execute("UPDATE folders SET last_synced=datetime('now') WHERE id=?", (folder["id"],))
    return total
