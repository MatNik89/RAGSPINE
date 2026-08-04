"""Piece F: AI sidebar (client_assist) + wizard kreiranje (legal_form, doc_types)."""
import pytest

from ragspine.business import client_assist, onboarding, quickref
from ragspine.docs.ingest import ingest_text


def test_assist_oib_rules(spine, cfg):
    a = client_assist.assist(spine, cfg, {"name": "X"})
    assert any("OIB" in s for s in a["suggestions"])
    a = client_assist.assist(spine, cfg, {"name": "X", "oib": "12345678901"})
    assert any("kontrolnu" in w for w in a["warnings"])


def test_assist_poduzece_pausal_inconsistent(spine, cfg):
    a = client_assist.assist(spine, cfg, {"legal_form": "poduzece", "regime": "pausal"})
    assert any("dobit" in w for w in a["warnings"])


def test_assist_pausal_uses_quickref_not_hardcode(spine, cfg):
    quickref.seed(spine)
    spine.set_override("quickref", "pausal_razred_1", "45000")  # admin izmjena
    a = client_assist.assist(spine, cfg, {"legal_form": "obrt", "regime": "pausal"})
    joined = " ".join(a["suggestions"])
    assert "45000" in joined  # override iz registra, ne hardkod


def test_assist_forms_and_employees(spine, cfg):
    a = client_assist.assist(spine, cfg, {"legal_form": "obrt", "has_employees": 1})
    assert any("Obrtni registar" in f for f in a["forms"])
    assert any("JOPPD" in f for f in a["forms"])
    assert any("HZMO" in f for f in a["forms"])


def test_assist_rag_sources_from_propisi(spine, cfg):
    quickref.seed(spine)
    ingest_text(spine, "Zakon o porezu na dodanu vrijednost. Prag za upis u registar "
                       "obveznika PDV-a iznosi 60.000 eura godišnjeg prometa. " * 5,
                title="Zakon o PDV-u")
    a = client_assist.assist(spine, cfg, {"name": "X"})  # bez pdv_status -> PDV upit
    assert any("PDV" in s["title"] for s in a["sources"])


class _FakeLLM:
    def complete(self, messages, system=None, **kw):
        assert "Ne izmišljaj" in system
        class R:
            text = "Provjeri paušalne razrede prije ugovaranja."
        return R()


def test_assist_llm_note_optional(spine, cfg):
    a = client_assist.assist(spine, cfg, {"legal_form": "obrt", "regime": "pausal"},
                             llm=_FakeLLM())
    assert a["llm_note"].startswith("Provjeri")
    a2 = client_assist.assist(spine, cfg, {"legal_form": "obrt", "regime": "pausal"})
    assert a2["llm_note"] is None  # bez LLM-a radi jednako, samo bez sažetka


def test_create_client_legal_form_and_doc_types(spine, cfg):
    res = onboarding.create_client(spine, cfg, {
        "name": "Obrt Mlin", "legal_form": "obrt", "regime": "pausal",
        "doc_types": ["osobna_iskaznica", "osobna_iskaznica", "ugovor"]}, owner="ana")
    row = spine.read().execute("SELECT legal_form FROM clients WHERE id=?",
                               (res["id"],)).fetchone()
    assert row["legal_form"] == "obrt"
    kinds = [r["doc_type_key"] for r in spine.read().execute(
        "SELECT doc_type_key FROM client_doc_types WHERE client_id=? ORDER BY doc_type_key",
        (res["id"],)).fetchall()]
    assert kinds == ["osobna_iskaznica", "ugovor"]  # dedup


def test_create_client_rejects_bad_combo(spine, cfg):
    with pytest.raises(ValueError):
        onboarding.create_client(spine, cfg, {"name": "X", "legal_form": "poduzece",
                                              "regime": "pausal"}, owner="ana")
    with pytest.raises(ValueError):
        onboarding.create_client(spine, cfg, {"name": "X", "legal_form": "zadruga"},
                                 owner="ana")


def test_api_assist_create_and_wizard_page(spine, cfg):
    from fastapi.testclient import TestClient
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user

    c = TestClient(create_app(spine, cfg))
    assert c.post("/clients/assist", json={}).status_code in (401, 403)
    add_user(spine, "ana", "pw")
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    a = c.post("/clients/assist", headers=h,
               json={"legal_form": "obrt", "has_employees": 1}).json()
    assert any("JOPPD" in f for f in a["forms"])

    r = c.post("/clients", headers=h, json={
        "name": "Pekara", "legal_form": "poduzece", "regime": "dobit",
        "doc_types": ["osobna_iskaznica"]})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert c.get(f"/clients/{cid}/doc-types", headers=h).json() == ["osobna_iskaznica"]

    r = c.get("/ui/novi-klijent", headers=h)
    assert r.status_code == 200 and "Dodaj novog klijenta" in r.text
