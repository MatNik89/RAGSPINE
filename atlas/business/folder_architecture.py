# Folder architecture (D2): NOTHING is invented in code — the structure is AGREED
# with the user (chat lane "arhitektura" or Settings) and stored in the database
# (config_overrides module 'arhitektura'). The source of truth for clients is the real
# KLIJENTI folder on the NAS (registered with role='klijenti'): each subfolder =
# a client. learn_structure() reads the EXISTING state as the starting point for the agreement.
# propose() is a read-only preview; apply() on confirmation creates ONLY what is missing.

import json
import os
import re

from atlas.business.onboarding import klijenti_root
from atlas.core import security

_MODULE = "arhitektura"
# folder name: no separators, no traversal, no control characters; Windows-safe
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# reserved DOS names (also with an extension, e.g. CON.txt) — Windows rejects them
_DOS_RESERVED = {"con", "prn", "aux", "nul",
                 *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_MAX_NAME = 100


def _root(cfg) -> str:
    return os.path.realpath(cfg.nas_root or cfg.data_dir)


def _subdirs(path: str, base: str) -> list:
    """Direct subfolders; a symlink that resolves outside base is skipped (fail-closed)."""
    out = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if (e.is_dir() and not os.path.islink(e.path)
                            and security.path_under(os.path.realpath(e.path), base)):
                        out.append(e)
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(out, key=lambda e: e.name.lower())


def learn_structure(spine, cfg) -> dict:
    """Read how the KLIJENTI folder is ALREADY organized: number of clients (subfolders)
    + the frequency of their child-subfolders. A starting point for the agreement, does not touch disk."""
    root = klijenti_root(spine, cfg)  # ValueError (unavailable) propagates to the caller
    if root is None:
        return {"root": None, "n_clients": 0, "subdir_counts": {}}
    counts: dict[str, int] = {}
    clients = _subdirs(root, root)
    for c in clients:
        for s in _subdirs(c.path, root):
            counts[s.name] = counts.get(s.name, 0) + 1
    ordered = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())))
    return {"root": root, "n_clients": len(clients), "subdir_counts": ordered}


def _valid_names(names) -> list[str]:
    out, seen = [], set()
    for n in names or []:
        if not isinstance(n, str):
            raise ValueError("ime mape mora biti string")
        n = n.strip().rstrip(".")  # NTFS strips a trailing dot — we do not allow it
        if (not n or n in (".", "..") or _BAD_NAME.search(n) or len(n) > _MAX_NAME
                or n.split(".")[0].lower() in _DOS_RESERVED):
            raise ValueError(f"nedozvoljeno ime mape: {n!r}")
        if n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append(n)
    return out


def get_template(spine) -> dict:
    """The agreed template: {'office': [...], 'client_subdirs': [...]}.
    Default EMPTY — until an agreement is made, nothing is proposed."""
    raw = spine.get_override(_MODULE, "template", "")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return {"office": list(data.get("office") or []),
            "client_subdirs": list(data.get("client_subdirs") or [])}


def set_template(spine, office=None, client_subdirs=None, user: str = "?") -> dict:
    """Save the agreement (None = do not touch that half). Returns the new template."""
    cur = get_template(spine)
    if office is not None:
        cur["office"] = _valid_names(office)
    if client_subdirs is not None:
        cur["client_subdirs"] = _valid_names(client_subdirs)
    spine.set_override(_MODULE, "template", json.dumps(cur, ensure_ascii=False))
    spine.audit(user, "arhitektura_template", json.dumps(cur, ensure_ascii=False))
    return cur


def _entry(root: str, path: str, name: str) -> dict | None:
    rp = os.path.realpath(path)
    if not security.path_under(rp, root):
        return None  # escape (../, symlink, another disk) — skip, fail-closed
    return {"name": name, "path": rp, "exists": os.path.isdir(rp)}


def propose(spine, cfg) -> dict:
    """Read-only preview from the AGREED template: office folders under the root,
    the agreed subfolders for each client from the real KLIJENTI folder (disk)."""
    tpl = get_template(spine)
    root = _root(cfg)
    must = [e for n in tpl["office"] if (e := _entry(root, os.path.join(root, n), n))]
    clients = []
    kroot = klijenti_root(spine, cfg)  # ValueError (unavailable) propagates — better than the wrong trees
    if kroot and tpl["client_subdirs"]:
        for c in _subdirs(kroot, kroot):
            subs = [e for s in tpl["client_subdirs"]
                    if (e := _entry(kroot, os.path.join(c.path, s), s))]
            clients.append({"name": c.name, "folder": os.path.realpath(c.path),
                            "folder_exists": True, "subdirs": subs})
    n_missing = (sum(1 for e in must if not e["exists"])
                 + sum(1 for c in clients for e in c["subdirs"] if not e["exists"]))
    return {"root": root, "klijenti_root": kroot, "template": tpl,
            "must_have": must, "clients": clients, "n_missing": n_missing}


def apply(spine, cfg, user: str = "?") -> dict:
    """Create what is missing from the agreed proposal. Idempotent; never
    deletes/moves. n_created matches n_missing from the preview."""
    prop = propose(spine, cfg)
    created = []
    try:
        for e in prop["must_have"]:
            if not e["exists"]:
                os.makedirs(e["path"], exist_ok=True)
                created.append(e["path"])
        for c in prop["clients"]:
            for e in c["subdirs"]:
                if not e["exists"]:
                    os.makedirs(e["path"], exist_ok=True)
                    created.append(e["path"])
    finally:
        # even a partial creation (failure on a later folder) must go into the audit
        if created:
            spine.audit(user, "folder_architecture_apply", f"created:{len(created)}")
    return {"created": created, "n_created": len(created)}


# --- chat lane: agreement through conversation --------------------------------
# "dogovor mape po klijentu: Ugovori, Izvodi" -> save client_subdirs
# "dogovor uredske mape: PROPISI, SCANNER"    -> save office
# anything else with an explicit architecture-intent -> overview of what was learned + agreed
# Admin-only (like the API): the lane saves state and reveals NAS paths/clients.

# ANCHORED to the whole query (fullmatch) — "in the quote 'dogovor mape...' do not" or
# a sentence tail after the list MUST NOT become filesystem configuration
_SET_CLIENT = re.compile(r"\s*dogovor\s+map[ae]\s+po\s+klijentu\s*:\s*([^.?!]+)[.?!]?\s*",
                         re.IGNORECASE)
_SET_OFFICE = re.compile(r"\s*dogovor\s+uredsk\w*\s+map[ae]\s*:\s*([^.?!]+)[.?!]?\s*",
                         re.IGNORECASE)


def _fmt_list(names: list[str]) -> str:
    return ", ".join(names) if names else "(nije dogovoreno)"


def _is_admin(actor) -> bool:
    from atlas.business.acl import ROLE_RANK
    return actor is not None and ROLE_RANK.get(actor.role or "", 0) >= ROLE_RANK["admin"]


def handle(spine, cfg, query: str, llm, actor=None) -> str:
    if not _is_admin(actor):
        return ("Arhitekturu mapa dogovara vlasnik/admin ureda — zamoli ih da to "
                "urede u chatu ili u Postavke → Arhitektura mapa.")
    user = getattr(actor, "username", "?") or "?"
    for pat, half in ((_SET_CLIENT, "client_subdirs"), (_SET_OFFICE, "office")):
        m = pat.fullmatch(query)
        if m:
            names = [n for n in (x.strip() for x in m.group(1).split(",")) if n]
            try:
                tpl = set_template(spine, **{half: names}, user=user)
            except ValueError as e:
                return f"Ne mogu spremiti: {e}"
            return ("Zapamtio sam dogovor. Uredske mape: "
                    f"{_fmt_list(tpl['office'])}. Po klijentu: "
                    f"{_fmt_list(tpl['client_subdirs'])}. Pregled i kreiranje: "
                    "Postavke → Arhitektura mapa.")
    try:
        learned = learn_structure(spine, cfg)
    except ValueError as e:
        return f"Mapa klijenata trenutno nije dostupna: {e}"
    tpl = get_template(spine)
    if learned["root"] is None:
        found = ("Nisam našao mapu klijenata — registriraj je u Postavke → "
                 "Mrežne mape s ulogom 'klijenti' (npr. KLIJENTI na NAS-u).")
    else:
        top = ", ".join(f"{k} ({v})" for k, v in list(learned["subdir_counts"].items())[:8])
        found = (f"Snimio sam {learned['root']}: {learned['n_clients']} klijenata. "
                 f"Najčešće podmape: {top or 'nema podmapa'}.")
    return (f"{found}\nTrenutni dogovor — uredske mape: {_fmt_list(tpl['office'])}; "
            f"po klijentu: {_fmt_list(tpl['client_subdirs'])}.\n"
            "Za spremanje reci: 'dogovor mape po klijentu: Ugovori, Izvodi, ...' "
            "ili 'dogovor uredske mape: PROPISI, SCANNER, ...'")


from atlas.rag import pipeline  # noqa: E402  (lazy: avoid any import-order coupling)
pipeline.LANE_HANDLERS["arhitektura"] = handle
