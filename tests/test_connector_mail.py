"""Mail adapteri (exchangelib / Graph) — connect logika, mockano (biblioteke
nisu instalirane u testu; live spoj traži atlas[mail] + prave podatke)."""
import sys
import types
from atlas.business import connector_adapters as ad


def test_exchange_without_lib_reports_install():
    st, detail = ad._test_exchange({"email": "a@b.hr", "password": "x"})
    assert st == "error" and "atlas[mail]" in detail


def test_graph_without_lib_reports_install():
    st, detail = ad._test_graph({"tenant_id": "t", "client_id": "c", "client_secret": "s", "mailbox": "a@b.hr"})
    assert st == "error" and "atlas[mail]" in detail


def test_exchange_bad_email():
    # exchangelib nedostaje → prvo javi install; format se provjerava tek s libom.
    st, _ = ad._test_exchange({"email": "nije-email", "password": "x"})
    assert st == "error"


def test_exchange_connected_mocked(monkeypatch):
    fake = types.ModuleType("exchangelib")
    fake.DELEGATE = "delegate"
    fake.Credentials = lambda **k: object()
    fake.Configuration = lambda **k: object()
    class _Inbox:  total_count = 7
    class _Acct:
        inbox = _Inbox()
        def __init__(self, **k): pass
    fake.Account = _Acct
    monkeypatch.setitem(sys.modules, "exchangelib", fake)
    st, detail = ad._test_exchange({"email": "a@b.hr", "password": "tajna"})
    assert st == "connected" and "7" in detail


def test_exchange_error_is_safe(monkeypatch):
    fake = types.ModuleType("exchangelib")
    fake.DELEGATE = "delegate"
    fake.Credentials = lambda **k: object()
    fake.Configuration = lambda **k: object()
    def _boom(**k): raise RuntimeError("auth failed for host")
    fake.Account = _boom
    monkeypatch.setitem(sys.modules, "exchangelib", fake)
    st, detail = ad._test_exchange({"email": "a@b.hr", "password": "SUPERTAJNA"})
    assert st == "error" and "SUPERTAJNA" not in detail  # lozinka ne curi


def test_graph_connected_mocked(monkeypatch):
    fake_msal = types.ModuleType("msal")
    class _App:
        def __init__(self, *a, **k): pass
        def acquire_token_for_client(self, scopes): return {"access_token": "tok"}
    fake_msal.ConfidentialClientApplication = _App
    fake_req = types.ModuleType("requests")
    class _R: status_code = 200; text = "ok"
    fake_req.get = lambda *a, **k: _R()
    monkeypatch.setitem(sys.modules, "msal", fake_msal)
    monkeypatch.setitem(sys.modules, "requests", fake_req)
    st, detail = ad._test_graph({"tenant_id": "t", "client_id": "c", "client_secret": "s", "mailbox": "a@b.hr"})
    assert st == "connected"


def test_graph_auth_fail_mocked(monkeypatch):
    fake_msal = types.ModuleType("msal")
    class _App:
        def __init__(self, *a, **k): pass
        def acquire_token_for_client(self, scopes): return {"error_description": "bad secret"}
    fake_msal.ConfidentialClientApplication = _App
    fake_req = types.ModuleType("requests"); fake_req.get = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "msal", fake_msal)
    monkeypatch.setitem(sys.modules, "requests", fake_req)
    st, detail = ad._test_graph({"tenant_id": "t", "client_id": "c", "client_secret": "s", "mailbox": "a@b.hr"})
    assert st == "error" and "bad secret" in detail
