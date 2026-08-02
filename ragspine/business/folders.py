# Registar mrežnih mapa: RAGSPINE vidi montirane korijene, izlista podmape,
# korisnik svakoj mapi dodijeli ulogu. Sve read-only i putanjski scoped ispod
# cfg.mount_roots (realpath+commonpath — simlink ne zaobilazi granicu).

import os

# Prijedlog uloga (role je slobodan string — dodaje se po potrebi, kao vrste obveza).
# 'propisi' = jedna glavna mapa; podmape (Zakoni/Pravilnici/Uredbe...) daju vrstu+autoritet.
ROLES = ("propisi", "klijenti", "ostalo")


def _under_a_root(rp: str, roots: list[str]) -> bool:
    for root in roots:
        if rp == root or os.path.commonpath([rp, root]) == root:
            return True
    return False


def _scoped(cfg, path: str) -> str:
    """realpath(path) koji mora biti jednak nekom mount_root ili ispod njega.
    Prazan mount_roots => sve odbij. Inače ValueError."""
    roots = cfg.mount_roots or []
    if not roots:
        raise ValueError("nema konfiguriranih mrežnih korijena (RAGSPINE_MOUNT_ROOTS)")
    rp = os.path.realpath(path or "")
    if not _under_a_root(rp, roots):
        raise ValueError(f"putanja izvan dozvoljenih korijena: {path!r}")
    return rp


def browse(cfg, path: str | None = None) -> dict:
    """Bez path-a: vrati dostupne korijene. S path-om (scoped): podmape te mape."""
    roots = cfg.mount_roots or []
    if not path:
        return {"path": None, "parent": None, "roots": roots, "dirs": []}
    rp = _scoped(cfg, path)
    if not os.path.isdir(rp):
        raise ValueError(f"nije direktorij: {path!r}")
    dirs = []
    with os.scandir(rp) as it:
        for entry in it:
            try:
                if entry.is_dir() and _under_a_root(os.path.realpath(entry.path), roots):
                    dirs.append(entry.name)
            except OSError:
                continue
    dirs.sort(key=str.lower)
    parent = os.path.dirname(rp)
    if not _under_a_root(parent, roots):
        parent = None  # ne daj izlaz iznad korijena
    return {"path": rp, "parent": parent, "roots": roots, "dirs": dirs}


def register(spine, cfg, path: str, role: str, label: str = "", user: str = "?") -> dict:
    rp = _scoped(cfg, path)
    if not os.path.isdir(rp):
        raise ValueError(f"mapa ne postoji: {path!r}")
    role = (role or "ostalo").strip() or "ostalo"
    label = (label or os.path.basename(rp)).strip()
    with spine.write() as c:
        c.execute(
            """INSERT INTO folders(path, role, label, added_by) VALUES(?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET role=excluded.role, label=excluded.label""",
            (rp, role, label, user),
        )
    spine.audit(user, "folder_register", f"folder:{rp}")
    return dict(spine.read().execute(
        "SELECT id, path, role, label, enabled, added_by, added_at FROM folders WHERE path=?",
        (rp,)).fetchone())


def list_folders(spine) -> list[dict]:
    return [dict(r) for r in spine.read().execute(
        "SELECT id, path, role, label, enabled, added_by, added_at FROM folders "
        "ORDER BY role, label COLLATE NOCASE").fetchall()]


def update(spine, folder_id: int, role: str | None = None, label: str | None = None,
           enabled: int | None = None, user: str = "?") -> dict:
    with spine.write() as c:
        if c.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone() is None:
            raise ValueError(f"nepoznata mapa: {folder_id}")
        sets, vals = [], []
        if role is not None:
            sets.append("role=?"); vals.append(role.strip() or "ostalo")
        if label is not None:
            sets.append("label=?"); vals.append(label.strip())
        if enabled is not None:
            sets.append("enabled=?"); vals.append(1 if enabled else 0)
        if sets:
            c.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id=?", (*vals, folder_id))
    spine.audit(user, "folder_update", f"folder:{folder_id}")
    return dict(spine.read().execute(
        "SELECT id, path, role, label, enabled, added_by, added_at FROM folders WHERE id=?",
        (folder_id,)).fetchone())


def remove(spine, folder_id: int, user: str = "?") -> None:
    with spine.write() as c:
        if c.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone() is None:
            raise ValueError(f"nepoznata mapa: {folder_id}")
        c.execute("DELETE FROM folders WHERE id=?", (folder_id,))  # ne dira disk
    spine.audit(user, "folder_remove", f"folder:{folder_id}")
