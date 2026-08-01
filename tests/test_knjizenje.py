from fastapi.testclient import TestClient

from ragspine.business import feedback_learn, kategorizacija, knjizenje
from ragspine.rag import pipeline
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def test_categorize_reprezentacija_half_deductible():
    r = kategorizacija.categorize("Račun za reprezentaciju u restoranu")
    assert r["matched"] is True
    assert r["porezno_priznato"] == 0.5
    assert "50" in r["note"] or "provjeri" in r["note"].lower()
    assert r["konto"]


def test_categorize_unknown_expense_falls_back():
    r = kategorizacija.categorize("nepoznati trošak xyz")
    assert r["matched"] is False
    assert r["konto"] == "6000"


def test_categorize_diacritic_insensitive():
    a = kategorizacija.categorize("racun za telefon i internet")
    b = kategorizacija.categorize("račun za telefon i internet")
    assert a["konto"] == b["konto"]
    assert a["matched"] and b["matched"]


def test_suggest_uses_rule_when_no_correction_exists(spine):
    r = knjizenje.suggest(spine, "kupnja uredskog materijala za ured")
    assert r["source"] == "pravilo"
    assert r["konto"]
    assert 0.0 <= r["porezno_priznato"] <= 1.0


def test_suggest_learns_from_correction_and_beats_rule(spine):
    desc = "racun za reprezentaciju u restoranu Zagreb"
    original = kategorizacija.categorize(desc)["konto"]
    feedback_learn.record_correction(spine, "ana", desc, original, "4099")

    similar = "reprezentacija restoran vecera s klijentom"
    suggestion = knjizenje.suggest(spine, similar)
    assert suggestion["source"] == "naučeno"
    assert suggestion["konto"] == "4099"
    first_confidence = suggestion["confidence"]

    feedback_learn.record_correction(spine, "ana", desc, original, "4099")
    suggestion2 = knjizenje.suggest(spine, similar)
    assert suggestion2["confidence"] > first_confidence


def test_record_correction_writes_audit(spine):
    cid = feedback_learn.record_correction(spine, "ana", "gorivo za auto", "4005", "4020")
    assert isinstance(cid, int) and cid > 0
    row = spine.read().execute(
        "SELECT * FROM audit_log WHERE action='konto_correction'"
    ).fetchone()
    assert row is not None and row["user"] == "ana"


def test_suggest_from_feedback_no_match_returns_none(spine):
    assert feedback_learn.suggest_from_feedback(spine, "potpuno nepovezan opis xyz") is None


def test_lane_handler_registered_and_answers(spine, cfg):
    assert "knjizenje" in pipeline.LANE_HANDLERS
    result = pipeline.answer(spine, cfg, "na koji konto knjižim uredski materijal?", "ana")
    assert result["lane"] == "knjizenje"
    assert result["answer"]
    assert "Prijedlog konta" in result["answer"]


def test_suggest_enriches_naziv_from_kontni_plan(spine):
    with spine.write() as c:
        c.execute("INSERT INTO kontni_plan(konto,naziv,razred) VALUES(?,?,?)",
                   ("4099", "Posebni troškovi", "4"))
    desc = "racun za reprezentaciju u restoranu Zagreb"
    original = kategorizacija.categorize(desc)["konto"]
    feedback_learn.record_correction(spine, "ana", desc, original, "4099")
    r = knjizenje.suggest(spine, "reprezentacija restoran vecera s klijentom")
    assert r["naziv"] == "Posebni troškovi"


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_api_knjizenje_suggest(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/knjizenje", json={"description": "uredski materijal papir"},
                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["konto"]


def test_api_knjizenje_correct(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/knjizenje/correct",
                json={"description": "gorivo za auto", "original_konto": "4005",
                      "corrected_konto": "4020"},
                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["learned"] is True
    assert body["id"]
