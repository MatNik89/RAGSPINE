import pytest

from atlas.business import doc_registry


def test_seed_osobna_iskaznica(spine):
    types = {t["key"]: t for t in doc_registry.list_types(spine)}
    oi = types["osobna_iskaznica"]
    assert oi["label"] == "Osobna iskaznica" and oi["active"] == 1
    fields = {f["key"]: f for f in oi["fields"]}
    assert set(fields) == {"broj", "datum_izdavanja", "mjesto_izdavanja", "datum_isteka"}
    assert fields["datum_isteka"]["kind"] == "date" and fields["datum_isteka"]["expiry"] is True
    assert fields["broj"]["kind"] == "text" and fields["broj"]["expiry"] is False


def test_upsert_normalizes_key_and_roundtrips(spine):
    key = doc_registry.upsert(spine, "Putna Isprava — Putovnica", "Putovnica", [
        {"key": "Broj Putovnice", "kind": "text"},
        {"key": "vrijedi do", "label": "Vrijedi do", "kind": "date", "expiry": True},
    ])
    assert key == "putna_isprava_putovnica"
    t = {x["key"]: x for x in doc_registry.list_types(spine)}[key]
    assert [f["key"] for f in t["fields"]] == ["broj_putovnice", "vrijedi_do"]
    assert t["fields"][0]["label"] == "broj_putovnice"  # label default = key
    assert t["fields"][1]["expiry"] is True


def test_upsert_overwrites_and_deactivates(spine):
    doc_registry.upsert(spine, "ugovor", "Ugovor", [])
    doc_registry.upsert(spine, "ugovor", "Ugovor o radu", [], active=False)
    t = {x["key"]: x for x in doc_registry.list_types(spine)}["ugovor"]
    assert t["label"] == "Ugovor o radu" and t["active"] == 0
    assert "ugovor" not in {x["key"] for x in doc_registry.list_types(spine, active_only=True)}


def test_upsert_validation(spine):
    with pytest.raises(ValueError):
        doc_registry.upsert(spine, "", "X", [])
    with pytest.raises(ValueError):
        doc_registry.upsert(spine, "x", "X", [{"key": "a", "kind": "broj"}])
    with pytest.raises(ValueError):
        doc_registry.upsert(spine, "x", "X", [{"key": "a", "kind": "text", "expiry": True}])
    with pytest.raises(ValueError):
        doc_registry.upsert(spine, "x", "X", [{"key": "a"}, {"key": "a"}])


def test_seed_respects_admin_changes(spine):
    doc_registry.upsert(spine, "osobna_iskaznica", "Osobna", [], active=False)
    t = {x["key"]: x for x in doc_registry.list_types(spine)}["osobna_iskaznica"]
    assert t["label"] == "Osobna" and t["fields"] == [] and t["active"] == 0


def test_export_json_shape(spine):
    out = doc_registry.export_json(spine)
    assert out["version"] == 1
    keys = [t["key"] for t in out["doc_types"]]
    assert "osobna_iskaznica" in keys


def test_upsert_rejects_non_string_key_and_label(spine):
    with pytest.raises(ValueError):
        doc_registry.upsert(spine, "x", "X", [{"key": 7, "kind": "text"}])
    with pytest.raises(ValueError):
        doc_registry.upsert(spine, "x", "X", [{"key": "a", "label": 7, "kind": "text"}])
