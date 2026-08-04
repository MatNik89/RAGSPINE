# Prijedlog arhitekture mapa (piece D): must-have mape ureda + standardne
# podmape po klijentu. propose() je read-only preview; apply() na potvrdu
# kreira SAMO nedostajuće (nikad ne briše/premješta). Svaki path guardan
# security.path_under — fail-closed na zli nas_folder ili drive-mismatch.

import os

from ragspine.business.onboarding import KLIJENTI_DIR
from ragspine.core import security

# ponytail: fiksne liste — upgrade path: editable template u Postavkama (G).
# KLIJENTI_DIR (malim slovima) dijeli se s onboardingom — case-sensitive NAS
# bi s 'KLIJENTI' dobio DRUGO stablo pored postojećeg 'klijenti'.
MUST_HAVE = (KLIJENTI_DIR, "PROPISI", "SCANNER", "ARHIVA")
CLIENT_SUBDIRS = ("Osobni dokumenti", "Ugovori", "Izvodi", "Računi", "Porezna")


def _root(cfg) -> str:
    return os.path.realpath(cfg.nas_root or cfg.data_dir)


def _entry(root: str, path: str, name: str) -> dict | None:
    rp = os.path.realpath(path)
    if not security.path_under(rp, root):
        return None  # escape (../, simlink, drugi disk) — preskoči, fail-closed
    return {"name": name, "path": rp, "exists": os.path.isdir(rp)}


def propose(spine, cfg) -> dict:
    """Read-only preview: što postoji, što fali. Ne dira disk."""
    root = _root(cfg)
    must = [e for n in MUST_HAVE if (e := _entry(root, os.path.join(root, n), n))]
    clients = []
    rows = spine.read().execute(
        "SELECT id, name, nas_folder FROM clients WHERE nas_folder IS NOT NULL "
        "AND nas_folder != '' ORDER BY name COLLATE NOCASE").fetchall()
    for r in rows:
        base = os.path.realpath(os.path.join(root, r["nas_folder"]))
        if not security.path_under(base, root):
            continue
        subs = [e for s in CLIENT_SUBDIRS
                if (e := _entry(root, os.path.join(base, s), s))]
        clients.append({"client_id": r["id"], "name": r["name"],
                        "folder": base, "folder_exists": os.path.isdir(base),
                        "subdirs": subs})
    n_missing = (sum(1 for e in must if not e["exists"])
                 + sum(1 for c in clients for e in c["subdirs"] if not e["exists"])
                 + sum(1 for c in clients if not c["folder_exists"]))
    return {"root": root, "must_have": must, "clients": clients, "n_missing": n_missing}


def apply(spine, cfg, user: str = "?") -> dict:
    """Kreiraj nedostajuće mape iz prijedloga. Idempotentno; vraća što je stvoreno.
    n_created odgovara n_missing iz preview-a (i baza klijenta se broji)."""
    prop = propose(spine, cfg)
    created = []
    try:
        for e in prop["must_have"]:
            if not e["exists"]:
                os.makedirs(e["path"], exist_ok=True)
                created.append(e["path"])
        for c in prop["clients"]:
            if not c["folder_exists"]:
                os.makedirs(c["folder"], exist_ok=True)
                created.append(c["folder"])
            for e in c["subdirs"]:
                if not e["exists"]:
                    os.makedirs(e["path"], exist_ok=True)
                    created.append(e["path"])
    finally:
        # i djelomično stvoreno (pad na kasnijoj mapi) mora u audit
        if created:
            spine.audit(user, "folder_architecture_apply", f"created:{len(created)}")
    return {"created": created, "n_created": len(created)}
