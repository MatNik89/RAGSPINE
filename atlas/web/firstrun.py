"""First-run onboarding gatekeeper. Uzor Open WebUI: onboarding je potreban dok
ne postoji nijedan korisnik (has_users()==False). Kad se u wizardu kreira prvi
operater, onboarding je gotov (prvi login ga čini ownerom preko resolve_login_org)."""

# Rute dostupne tijekom onboardinga (sve ostalo navigacijsko preusmjerava na /ui/setup)
SETUP_ALLOW = ("/ui/setup", "/setup", "/preflight", "/login", "/auth",
               "/static", "/health", "/favicon")


def needs_onboarding(spine) -> bool:
    return spine.read().execute("SELECT 1 FROM users LIMIT 1").fetchone() is None


def needs_setup(spine) -> bool:
    """Setup nije gotov dok wizard ne postavi setup_complete. Vezano na flag,
    NE na postojanje korisnika (admin se kreira usred wizarda — Codex/hermes)."""
    from atlas.ops import wizard_state
    return not wizard_state.is_complete(spine)


def _redirect_target(path: str) -> bool:
    """Je li ovo navigacijska (HTML) ruta koju treba preusmjeriti na wizard.
    Točan match ili prefiks s '/' (Codex: goli startswith dopušta /ui/setupX)."""
    if any(path == p or path.startswith(p + "/") for p in SETUP_ALLOW):
        return False
    return path == "/" or path == "/obveze" or path.startswith("/ui/")


def create_first_owner(spine, username: str, password: str) -> None:
    """Atomsko kreiranje prvog korisnika: BEGIN IMMEDIATE uzme write-lock pa je
    provjera-praznine + insert jedna transakcija (Codex: inače dva paralelna
    zahtjeva stvore dva ownera). ValueError ako korisnik već postoji."""
    from atlas.core.security import hash_password
    with spine.write() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            if c.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise ValueError("operater već postoji")
            c.execute("INSERT INTO users(username, pw_hash, role) VALUES(?,?,'radnik')",
                      (username, hash_password(password)))
        except Exception:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")
