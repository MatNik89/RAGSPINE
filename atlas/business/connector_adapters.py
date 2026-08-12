"""Registration of connector types (mail + channels) with field schemas and a test function.
The actual send/receive logic comes per adapter; here are the definitions + a test
that checks library availability and (where cheap) basic correctness.

Email only: M365 Graph + on-prem Exchange (exchangelib). Message channels
(Telegram/WhatsApp/Viber) have been dropped — the user gave up on them."""
import importlib


from atlas.business.connectors import ConnectorType, Field, register


def _lib(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def _safe_err(e: Exception) -> str:
    """Short, safe error message (without echoing config/password — Codex)."""
    return f"{type(e).__name__}: {str(e)[:180]}"


# --- Mail: on-prem Exchange (EWS autodiscover) ---
def _test_exchange(cfg):
    if not _lib("exchangelib"):
        return "error", "instaliraj: pip install atlas[mail]"
    email = (cfg.get("email") or "").strip()
    if "@" not in email:
        return "error", "email nije ispravan"
    try:
        from exchangelib import DELEGATE, Account, Configuration, Credentials
        creds = Credentials(username=email, password=cfg.get("password", ""))
        server = (cfg.get("server") or "").strip()
        if server:
            conf = Configuration(server=server, credentials=creds)
            acct = Account(primary_smtp_address=email, config=conf, access_type=DELEGATE)
        else:
            acct = Account(primary_smtp_address=email, credentials=creds,
                           autodiscover=True, access_type=DELEGATE)
        n = acct.inbox.total_count  # a cheap call that forces the connection
        return "connected", f"spojeno (inbox: {n})"
    except Exception as e:
        return "error", _safe_err(e)


# --- Mail: Microsoft 365 (Graph API, OAuth app) ---
def _test_graph(cfg):
    if not (_lib("msal") and _lib("requests")):
        return "error", "instaliraj: pip install atlas[mail]"
    tenant = (cfg.get("tenant_id") or "").strip()
    mailbox = (cfg.get("mailbox") or "").strip()
    try:
        import msal
        import requests
        app = msal.ConfidentialClientApplication(
            (cfg.get("client_id") or "").strip(),
            authority=f"https://login.microsoftonline.com/{tenant}",
            client_credential=cfg.get("client_secret", ""))
        tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in tok:
            return "error", (tok.get("error_description") or "autentikacija neuspješna")[:180]
        r = requests.get(
            f"https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/inbox",
            headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=15)
        if r.status_code == 200:
            return "connected", "spojeno (Graph)"
        return "error", f"Graph {r.status_code}"
    except Exception as e:
        return "error", _safe_err(e)


# --- Telegram gateway (official Bot API — access to ATLAS via a bot) ---
def _test_telegram_gateway(cfg):
    from atlas.business.telegram_gateway import TelegramClient
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        return "error", "bot_token je obavezan (od @BotFather)"
    try:
        me = TelegramClient(token).get_me()
        if me.get("ok"):
            return "connected", f"bot @{me['result'].get('username', '?')}"
        return "error", "token odbijen"
    except Exception as e:
        return "error", _safe_err(e)


def _test_imap(cfg):
    import imaplib
    host = (cfg.get("host") or "").strip()
    email_addr = (cfg.get("email") or "").strip()
    if "@" not in email_addr or not host:
        return "error", "host i e-mail su obavezni"
    try:
        from atlas.core.net import _is_blocked_addr
        import socket
        if any(_is_blocked_addr(sa[4][0]) for sa in socket.getaddrinfo(host, None)):
            return "error", "host je interni/loopback — odbijeno"
        imap = imaplib.IMAP4_SSL(host)
        imap.login(email_addr, cfg.get("password", ""))
        imap.logout()
        return "connected", "prijava uspješna"
    except Exception as e:
        return "error", _safe_err(e)


def register_builtin() -> None:
    register(ConnectorType(
        kind="telegram_gateway", label="Telegram (pristup ATLAS-u preko bota)", category="kanal",
        fields=[Field("bot_token", "Bot token (od @BotFather)", type="password", secret=True)],
        test=_test_telegram_gateway))
    register(ConnectorType(
        kind="mail_imap", label="E-pošta — IMAP (standardni server)", category="mail",
        fields=[Field("host", "IMAP server (npr. imap.gmail.com)"),
                Field("email", "E-mail adresa"),
                Field("password", "Lozinka / app-lozinka", type="password", secret=True),
                Field("folder", "Mapa (default INBOX)", required=False)],
        test=_test_imap))
    register(ConnectorType(
        kind="mail_exchange", label="E-pošta — Exchange (server)", category="mail",
        fields=[Field("server", "Server (opcijski, autodiscover)", required=False),
                Field("email", "E-mail adresa"),
                Field("password", "Lozinka", type="password", secret=True)],
        test=_test_exchange))
    register(ConnectorType(
        kind="mail_graph", label="E-pošta — Microsoft 365 (Graph)", category="mail",
        fields=[Field("tenant_id", "Tenant ID"),
                Field("client_id", "Client ID (app)"),
                Field("client_secret", "Client Secret", type="password", secret=True),
                Field("mailbox", "Poštanski sandučić (e-mail)")],
        test=_test_graph))
