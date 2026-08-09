"""Registar alata agentskog sloja: shema, validacija, ovlasti, izvršenje."""
import pytest

from atlas.business import acl, client_visibility as cv, tenancy
from atlas.docs import ingest as ing
from atlas.rag import agent_tools as at
from atlas.web import deps

VALID_OIB = "10000000000"
BAD_OIB = "12345678901"


def _actor(spine, username="ana", role="member", user_id=1):
    deps.add_user(spine, username, "pw")
    org_id = tenancy.default_org_id(spine)
    return acl.Actor(user_id=user_id, org_id=org_id, role=role, username=username)


def _uid(spine, username):
    return spine.read().execute(
        "SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]


def _mk_client(spine, name="Pekara Mlinar", oib=None):
    with spine.write() as c:
        return c.execute(
            "INSERT INTO clients(name, oib) VALUES(?,?)", (name, oib)).lastrowid


# --- TOOLS / schema shape ------------------------------------------------

def test_tools_registry_has_all_first_round_tools():
    expected = {"pretrazi", "popis_obveza", "stanje_klijenta", "dodaj_klijenta",
                "uredi_klijenta", "oznaci_obvezu", "zakazi_rok", "zapisi_belesku"}
    assert expected <= set(at.TOOLS)  # prvi krug alata i dalje postoji; registar raste (Faza 2)


def test_readonly_flags_match_spec():
    for name in ("pretrazi", "popis_obveza", "stanje_klijenta"):
        assert at.TOOLS[name].readonly is True
    for name in ("dodaj_klijenta", "uredi_klijenta", "oznaci_obvezu", "zakazi_rok", "zapisi_belesku"):
        assert at.TOOLS[name].readonly is False


def test_schema_is_plain_dict_subset():
    schema = at.TOOLS["zakazi_rok"].schema
    assert schema["type"] == "object"
    assert "datum" in schema["properties"]
    assert schema["required"] == ["klijent", "vrsta", "datum"]


# --- validate() -----------------------------------------------------------

def test_validate_unknown_tool():
    ok, err = at.validate("ne_postoji", {})
    assert not ok and "nepoznat" in err


def test_validate_missing_required_field():
    ok, err = at.validate("zapisi_belesku", {"klijent": "Alfa"})
    assert not ok and "tekst" in err


def test_validate_rejects_bad_oib():
    ok, err = at.validate("dodaj_klijenta", {"naziv": "Test", "oib": BAD_OIB})
    assert not ok and "OIB" in err


def test_validate_accepts_good_oib():
    ok, err = at.validate("dodaj_klijenta", {"naziv": "Test", "oib": VALID_OIB})
    assert ok and err is None


def test_validate_rejects_bad_date():
    ok, err = at.validate("zakazi_rok", {"klijent": "Alfa", "vrsta": "osobna", "datum": "31-12-2026"})
    assert not ok and "datum" in err.lower()


def test_validate_accepts_iso_date():
    ok, err = at.validate("zakazi_rok", {"klijent": "Alfa", "vrsta": "osobna", "datum": "2026-12-31"})
    assert ok and err is None


def test_validate_rejects_wrong_type():
    ok, err = at.validate("pretrazi", {"upit": "pdv", "web": "da"})
    assert not ok and "web" in err


def test_validate_uredi_klijenta_rejects_unknown_field():
    ok, err = at.validate("uredi_klijenta", {"kljuc": "10000000000", "polja": {"owner": "netko"}})
    assert not ok and "polja" in err.lower() or "owner" in str(err)


def test_validate_uredi_klijenta_accepts_known_field():
    ok, err = at.validate("uredi_klijenta", {"kljuc": "10000000000", "polja": {"email": "a@b.hr"}})
    assert ok and err is None


# --- allowed() -------------------------------------------------------------

def test_allowed_viewer_can_read(spine):
    a = _actor(spine, role="viewer")
    assert at.allowed(a, "pretrazi")
    assert at.allowed(a, "stanje_klijenta")


def test_allowed_viewer_cannot_write(spine):
    a = _actor(spine, role="viewer")
    assert not at.allowed(a, "dodaj_klijenta")
    assert not at.allowed(a, "zapisi_belesku")


def test_allowed_member_can_write(spine):
    a = _actor(spine, role="member")
    assert at.allowed(a, "dodaj_klijenta")


def test_allowed_unknown_tool_false(spine):
    a = _actor(spine, role="owner")
    assert not at.allowed(a, "ne_postoji")


# --- run_tool: unknown / disallowed ----------------------------------------

def test_run_tool_unknown_raises(spine, cfg):
    a = _actor(spine)
    with pytest.raises(ValueError):
        at.run_tool(spine, cfg, a, "ne_postoji", {})


def test_run_tool_disallowed_role_raises(spine, cfg):
    a = _actor(spine, role="viewer")
    with pytest.raises(ValueError):
        at.run_tool(spine, cfg, a, "dodaj_klijenta", {"naziv": "Test"})


def test_run_tool_bad_args_raises(spine, cfg):
    a = _actor(spine)
    with pytest.raises(ValueError):
        at.run_tool(spine, cfg, a, "dodaj_klijenta", {"naziv": "Test", "oib": BAD_OIB})


# --- readonly execution -----------------------------------------------------

def test_pretrazi_falls_back_to_web_when_no_local_hits(spine, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(at.websearch, "ddg", lambda q, **kw: calls.append(q) or [{"title": "x", "url": "u"}])
    a = _actor(spine)
    out = at.run_tool(spine, cfg, a, "pretrazi", {"upit": "nešto što ne postoji"})
    assert out["lokalno"] == []
    assert out["web"] == [{"title": "x", "url": "u"}]
    assert calls == ["nešto što ne postoji"]


def test_pretrazi_skips_web_when_local_hits_and_not_requested(spine, cfg, monkeypatch):
    org_id = tenancy.default_org_id(spine)
    ing.ingest_text(spine, "Stopa PDV-a u Hrvatskoj je 25 posto.", "pdv-stope",
                    doc_type="zakon", org_id=org_id)
    called = []
    monkeypatch.setattr(at.websearch, "ddg", lambda q, **kw: called.append(q) or [])
    a = _actor(spine)
    out = at.run_tool(spine, cfg, a, "pretrazi", {"upit": "kolika je stopa PDV-a"})
    assert out["lokalno"]
    assert not called


def test_stanje_klijenta_returns_karton(spine, cfg):
    cid = _mk_client(spine, "Pekara Mlinar", VALID_OIB)
    a = _actor(spine)
    out = at.run_tool(spine, cfg, a, "stanje_klijenta", {"kljuc": VALID_OIB})
    assert out["client"]["id"] == cid
    assert out["client"]["name"] == "Pekara Mlinar"


def test_stanje_klijenta_unknown_raises(spine, cfg):
    a = _actor(spine)
    with pytest.raises(ValueError):
        at.run_tool(spine, cfg, a, "stanje_klijenta", {"kljuc": "nepostojeci"})


def test_popis_obveza_filters_by_vrsta_and_sent(spine, cfg):
    cid = _mk_client(spine, "Pekara Mlinar")
    with spine.write() as c:
        c.execute("UPDATE clients SET pdv_status='U sustavu PDV-a', pdv_freq='monthly', active=1 WHERE id=?", (cid,))
    a = _actor(spine)
    out = at.run_tool(spine, cfg, a, "popis_obveza", {"vrsta": "PDV"})
    assert any(o["client_id"] == cid for o in out["obveze"])

    at.run_tool(spine, cfg, a, "oznaci_obvezu", {"klijent": "Pekara Mlinar", "vrsta": "PDV"})
    out2 = at.run_tool(spine, cfg, a, "popis_obveza", {"vrsta": "PDV", "samo_neposlano": True})
    assert all(o["client_id"] != cid for o in out2["obveze"])


# --- write execution: calls business layer with right args ----------------

def test_dodaj_klijenta_creates_row(spine, cfg):
    a = _actor(spine)
    out = at.run_tool(spine, cfg, a, "dodaj_klijenta", {"naziv": "Nova Firma", "oib": VALID_OIB})
    row = spine.read().execute("SELECT * FROM clients WHERE id=?", (out["id"],)).fetchone()
    assert row["name"] == "Nova Firma"
    assert row["oib"] == VALID_OIB


def test_dodaj_klijenta_grants_visibility_to_restricted_creator(spine, cfg):
    a = _actor(spine, username="boris", role="member")
    cv.set_policy(spine, a.user_id, sees_all=False, client_ids=[])
    out = at.run_tool(spine, cfg, a, "dodaj_klijenta", {"naziv": "Vlastita Firma"})
    assert cv.can_see(spine, a.user_id, out["id"], a.role)


def test_uredi_klijenta_updates_field(spine, cfg):
    cid = _mk_client(spine, "Pekara Mlinar", VALID_OIB)
    a = _actor(spine)
    at.run_tool(spine, cfg, a, "uredi_klijenta", {"kljuc": VALID_OIB, "polja": {"email": "nova@primjer.hr"}})
    row = spine.read().execute("SELECT email FROM clients WHERE id=?", (cid,)).fetchone()
    assert row["email"] == "nova@primjer.hr"


def test_zapisi_belesku_inserts_note(spine, cfg):
    _mk_client(spine, "Pekara Mlinar", VALID_OIB)
    a = _actor(spine, username="ana")
    out = at.run_tool(spine, cfg, a, "zapisi_belesku", {"klijent": "Pekara Mlinar", "tekst": "Nazvao klijent."})
    row = spine.read().execute("SELECT * FROM notes WHERE id=?", (out["id"],)).fetchone()
    assert row["body"] == "Nazvao klijent."
    assert row["author"] == "ana"


def test_zakazi_rok_inserts_expiry_item(spine, cfg):
    cid = _mk_client(spine, "Pekara Mlinar", VALID_OIB)
    a = _actor(spine)
    out = at.run_tool(spine, cfg, a, "zakazi_rok",
                      {"klijent": "Pekara Mlinar", "vrsta": "osobna", "datum": "2026-12-31"})
    row = spine.read().execute("SELECT * FROM expiry_items WHERE id=?", (out["id"],)).fetchone()
    assert row["client_id"] == cid
    assert row["expires"] == "2026-12-31"


def test_oznaci_obvezu_marks_sent(spine, cfg):
    cid = _mk_client(spine, "Pekara Mlinar")
    with spine.write() as c:
        c.execute("UPDATE clients SET pdv_status='U sustavu PDV-a', active=1 WHERE id=?", (cid,))
    a = _actor(spine)
    out = at.run_tool(spine, cfg, a, "oznaci_obvezu", {"klijent": "Pekara Mlinar", "vrsta": "PDV"})
    row = spine.read().execute(
        "SELECT sent FROM obligation_status WHERE obligation_id=?", (out["obligation_id"],)).fetchone()
    assert row["sent"] == 1


def test_oznaci_obvezu_unknown_obligation_raises(spine, cfg):
    _mk_client(spine, "Pekara Mlinar")
    a = _actor(spine)
    with pytest.raises(ValueError):
        at.run_tool(spine, cfg, a, "oznaci_obvezu", {"klijent": "Pekara Mlinar", "vrsta": "PDV"})


# --- vidljivost: restringiran radnik ne smije tuđeg klijenta ---------------

def test_restricted_worker_cannot_write_hidden_client(spine, cfg):
    visible_id = _mk_client(spine, "Alfa")
    hidden_id = _mk_client(spine, "Beta")
    a = _actor(spine, username="boris", role="member")
    cv.set_policy(spine, a.user_id, sees_all=False, client_ids=[visible_id])
    with pytest.raises(ValueError):
        at.run_tool(spine, cfg, a, "zapisi_belesku", {"klijent": "Beta", "tekst": "x"})
    # ali smije vlastiti vidljivi klijent
    out = at.run_tool(spine, cfg, a, "zapisi_belesku", {"klijent": "Alfa", "tekst": "ok"})
    assert out["id"] > 0
    assert hidden_id  # referenced, sanity


def test_restricted_worker_cannot_read_hidden_client_state(spine, cfg):
    hidden_id = _mk_client(spine, "Beta")
    a = _actor(spine, username="boris", role="member")
    cv.set_policy(spine, a.user_id, sees_all=False, client_ids=[])
    with pytest.raises(ValueError):
        at.run_tool(spine, cfg, a, "stanje_klijenta", {"kljuc": "Beta"})
    assert hidden_id  # sanity
