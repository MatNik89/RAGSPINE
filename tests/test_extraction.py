import pytest

from ragspine.business import doc_registry
from ragspine.docs import extraction
from ragspine.docs.ingest import ingest_text

_OI_FIELDS = [
    {"key": "broj", "label": "Broj", "kind": "text", "expiry": False},
    {"key": "datum_izdavanja", "label": "Datum izdavanja", "kind": "date", "expiry": False},
    {"key": "mjesto_izdavanja", "label": "Mjesto izdavanja", "kind": "text", "expiry": False},
    {"key": "datum_isteka", "label": "Datum isteka", "kind": "date", "expiry": True},
]

_TEXT = """REPUBLIKA HRVATSKA — OSOBNA ISKAZNICA
Broj: 115362299
Datum izdavanja: 15. 8. 2021.
Mjesto izdavanja: Zagreb
Datum isteka: 15.08.2026
"""


def test_extract_regex_croatian_dates_and_text():
    vals = extraction.extract_regex(_TEXT, _OI_FIELDS)
    assert vals == {"broj": "115362299", "datum_izdavanja": "2021-08-15",
                    "mjesto_izdavanja": "Zagreb", "datum_isteka": "2026-08-15"}


def test_extract_regex_missing_fields_absent():
    vals = extraction.extract_regex("Broj: 42", _OI_FIELDS)
    assert vals == {"broj": "42"}


class _FakeLLM:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def complete(self, messages, system=None, **kw):
        self.calls.append((messages, system))
        class R:  # noqa: N801 — mini rezultat
            text = self._text
        return R()


def test_extract_llm_fills_only_missing_and_validates_dates():
    llm = _FakeLLM('```json\n{"mjesto_izdavanja": "Split", "datum_isteka": "31.12.2027", '
                   '"broj": null}\n```')
    missing = [f for f in _OI_FIELDS if f["key"] in ("mjesto_izdavanja", "datum_isteka", "broj")]
    vals = extraction.extract_llm(llm, "tekst", missing)
    assert vals == {"mjesto_izdavanja": "Split", "datum_isteka": "2027-12-31"}


def test_extract_llm_garbage_and_none_degrade():
    assert extraction.extract_llm(None, "t", _OI_FIELDS) == {}
    assert extraction.extract_llm(_FakeLLM("nije json"), "t", _OI_FIELDS) == {}
    assert extraction.extract_llm(_FakeLLM('["lista"]'), "t", _OI_FIELDS) == {}


def _mk_doc(spine, client_id=None):
    doc_id = ingest_text(spine, _TEXT * 3, title="osobna.pdf", client_id=client_id)
    assert doc_id is not None
    return doc_id


def test_extract_e2e_regex_only_creates_expiry(spine, cfg):
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name) VALUES('Perić Pero')").lastrowid
    doc_id = _mk_doc(spine, client_id=cid)
    res = extraction.extract(spine, cfg, doc_id, "osobna_iskaznica", llm=None)
    assert res["fields"]["datum_isteka"] == "2026-08-15"
    assert res["engines"]["broj"] == "regex"
    assert res["expiry_created"] == 1
    row = spine.read().execute("SELECT * FROM expiry_items WHERE client_id=?", (cid,)).fetchone()
    assert row["expires"] == "2026-08-15" and row["kind"] == "osobna_iskaznica"
    assert row["label"] == "Osobna iskaznica: Datum isteka"
    # re-ekstrakcija: bez duplikata, bez promjene -> expiry_created 0
    res2 = extraction.extract(spine, cfg, doc_id, "osobna_iskaznica", llm=None)
    assert res2["expiry_created"] == 0
    n = spine.read().execute("SELECT COUNT(*) AS n FROM expiry_items").fetchone()["n"]
    assert n == 1


def test_extract_llm_fallback_merges(spine, cfg):
    doc_id = ingest_text(spine, "Broj: 99887766\nnema ostalih podataka " * 20,
                         title="djelomicna.pdf")
    llm = _FakeLLM('{"datum_izdavanja": null, "mjesto_izdavanja": "Rijeka", '
                   '"datum_isteka": "2027-01-05"}')
    res = extraction.extract(spine, cfg, doc_id, "osobna_iskaznica", llm=llm)
    assert res["fields"]["broj"] == "99887766" and res["engines"]["broj"] == "regex"
    assert res["fields"]["mjesto_izdavanja"] == "Rijeka" and res["engines"]["mjesto_izdavanja"] == "llm"
    # LLM je dobio SAMO polja koja regex nije našao
    _msgs, system = llm.calls[0]
    assert '- "broj"' not in system and '- "mjesto_izdavanja"' in system
    # bez client_id -> nema expiry reda
    assert res["expiry_created"] == 0
    n = spine.read().execute("SELECT COUNT(*) AS n FROM expiry_items").fetchone()["n"]
    assert n == 0


def test_extract_unknown_doc_or_type(spine, cfg):
    with pytest.raises(ValueError):
        extraction.extract(spine, cfg, 9999, "osobna_iskaznica")
    doc_id = _mk_doc(spine)
    with pytest.raises(ValueError):
        extraction.extract(spine, cfg, doc_id, "nema_takve")


def test_extract_stores_last_extract(spine, cfg):
    doc_id = _mk_doc(spine)
    extraction.extract(spine, cfg, doc_id, "osobna_iskaznica")
    row = spine.read().execute("SELECT * FROM doc_extracts WHERE doc_id=?", (doc_id,)).fetchone()
    assert row is not None and row["doc_type_key"] == "osobna_iskaznica"
    assert "2026-08-15" in row["fields_json"]


def test_extracted_expiry_reaches_dashboard_with_warn(spine, cfg):
    # rok-alert lanac: ekstrakcija -> expiry_items -> dashboard warn (<=7 dana)
    from datetime import date, timedelta
    from ragspine.business import dashboard
    soon = (date.today() + timedelta(days=5)).strftime("%d.%m.%Y")
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name) VALUES('Ana Anić')").lastrowid
    doc_id = ingest_text(spine, f"Broj: 777\nDatum isteka: {soon}\n" * 10,
                         title="osobna-ana.pdf", client_id=cid)
    extraction.extract(spine, cfg, doc_id, "osobna_iskaznica")
    rows = dashboard.home_data(spine)["expiring"]
    mine = [r for r in rows if r["client_id"] == cid]
    assert mine and mine[0]["state"] == "warn" and mine[0]["days_left"] == 5
