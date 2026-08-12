"""First-run onboarding gatekeeper. Modeled on Open WebUI: onboarding is needed
as long as no user exists (has_users()==False). Once the first operator is
created in the wizard, onboarding is done (the first login makes them owner via
resolve_login_org)."""

# Routes available during onboarding (everything else navigational redirects to /ui/setup)
SETUP_ALLOW = ("/ui/setup", "/setup", "/preflight", "/login", "/auth",
               "/static", "/health", "/favicon")


def needs_onboarding(spine) -> bool:
    return spine.read().execute("SELECT 1 FROM users LIMIT 1").fetchone() is None


def needs_setup(spine) -> bool:
    """Setup is not done until the wizard sets setup_complete. Tied to the flag,
    NOT to the existence of a user (the admin is created mid-wizard — Codex/hermes)."""
    from atlas.ops import wizard_state
    return not wizard_state.is_complete(spine)


def _redirect_target(path: str) -> bool:
    """Whether this is a navigational (HTML) route that should be redirected to
    the wizard. Exact match or prefix with '/' (Codex: a bare startswith allows /ui/setupX)."""
    if any(path == p or path.startswith(p + "/") for p in SETUP_ALLOW):
        return False
    return path == "/" or path == "/obveze" or path.startswith("/ui/")


def create_first_owner(spine, username: str, password: str) -> None:
    """Atomic creation of the first user: BEGIN IMMEDIATE takes a write-lock so the
    emptiness-check + insert is a single transaction (Codex: otherwise two parallel
    requests create two owners). ValueError if the user already exists."""
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
