"""Vidljivost klijenata po radniku (unutar jednog ureda). Zadano radnik vidi
SVE klijente; admin/vlasnik uvijek sve. Opcijom se radniku ograniči vidljivost
na izabrane klijente. Reuse ACL rang-a: manager (admin/owner) je iznad pravila.

Napomena o modelu: svaka firma ima VLASTITI RAGSPINE install (vlastita baza),
pa cross-org izolacija ovdje nije tema — ovo je čisto intra-uredska vidljivost.
"""
from ragspine.business.acl import ROLE_RANK


def _is_manager(role: str) -> bool:
    return ROLE_RANK.get(role or "", 0) >= ROLE_RANK["admin"]


def visible_ids(spine, user_id: int, role: str = "member") -> set | None:
    """None = bez ograničenja (svi klijenti). Inače skup dozvoljenih client_id."""
    if _is_manager(role):
        return None
    r = spine.read().execute(
        "SELECT sees_all_clients FROM users WHERE id=?", (user_id,)).fetchone()
    if r is None or r["sees_all_clients"]:
        return None
    rows = spine.read().execute(
        "SELECT client_id FROM client_visibility WHERE user_id=?", (user_id,)).fetchall()
    return {row["client_id"] for row in rows}


def can_see(spine, user_id: int, client_id: int, role: str = "member") -> bool:
    ids = visible_ids(spine, user_id, role)
    return ids is None or client_id in ids


def get_policy(spine, user_id: int) -> dict:
    r = spine.read().execute(
        "SELECT sees_all_clients FROM users WHERE id=?", (user_id,)).fetchone()
    sees_all = bool(r["sees_all_clients"]) if r else True
    ids = [row["client_id"] for row in spine.read().execute(
        "SELECT client_id FROM client_visibility WHERE user_id=?", (user_id,)).fetchall()]
    return {"sees_all": sees_all, "client_ids": ids}


def set_policy(spine, user_id: int, sees_all: bool,
               client_ids: list[int] | None = None, actor_name: str = "?") -> None:
    with spine.write() as c:
        c.execute("UPDATE users SET sees_all_clients=? WHERE id=?",
                  (1 if sees_all else 0, user_id))
        c.execute("DELETE FROM client_visibility WHERE user_id=?", (user_id,))
        if not sees_all:
            for cid in (client_ids or []):
                c.execute("INSERT OR IGNORE INTO client_visibility(user_id, client_id) VALUES(?,?)",
                          (user_id, cid))
    spine.audit(actor_name, "client_visibility_set", f"user:{user_id}:all={int(sees_all)}")


def grant(spine, user_id: int, client_id: int, actor_name: str = "?") -> None:
    """Dodaj jednog klijenta radnikovoj vidljivosti (npr. onome tko ga je kreirao,
    da restringirani radnik vidi vlastito djelo)."""
    with spine.write() as c:
        c.execute("INSERT OR IGNORE INTO client_visibility(user_id, client_id) VALUES(?,?)",
                  (user_id, client_id))
    spine.audit(actor_name, "client_visibility_grant", f"user:{user_id}:client:{client_id}")
