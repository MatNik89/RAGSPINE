"""Registracija konektor-tipova (mail + kanali) sa shemama polja i test funkcijom.
Stvarna logika slanja/primanja dolazi po adapteru; ovdje su definicije + test
koji provjerava dostupnost biblioteke i (gdje jeftino) osnovnu ispravnost.

Samo e-pošta: M365 Graph + on-prem Exchange (exchangelib). Kanali poruka
(Telegram/WhatsApp/Viber) su izbačeni — korisnik odustao."""
import importlib

from ragspine.business.connectors import ConnectorType, Field, register


def _lib(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


# --- Mail: on-prem Exchange (EWS autodiscover) ---
def _test_exchange(cfg):
    if not _lib("exchangelib"):
        return "error", "instaliraj: pip install exchangelib"
    # stvarni connect dolazi u mail adapteru; zasad potvrdi format
    if "@" not in cfg.get("email", ""):
        return "error", "email nije ispravan"
    return "pending", "spremno za spajanje (mail adapter u izradi)"


# --- Mail: Microsoft 365 (Graph API, OAuth app) ---
def _test_graph(cfg):
    if not (_lib("msal") and _lib("requests")):
        return "error", "instaliraj: pip install msal requests"
    return "pending", "spremno za OAuth (mail adapter u izradi)"


def register_builtin() -> None:
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
