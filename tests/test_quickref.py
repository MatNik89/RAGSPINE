from ragspine.business import quickref


def test_seed_inserts_all_then_zero(spine):
    n = quickref.seed(spine)
    assert n == len(quickref.SEED)
    assert quickref.seed(spine) == 0


def test_search_returns_value_and_source(spine):
    quickref.seed(spine)
    results = quickref.search(spine, "minimalac")
    assert len(results) >= 1
    row = results[0]
    assert row["value"]
    assert row["source"]


def test_override_changes_search_value(spine):
    quickref.seed(spine)
    row = quickref.search(spine, "minimalac")[0]
    spine.set_override("quickref", row["key"], "999.99")
    updated = quickref.search(spine, "minimalac")[0]
    assert updated["value"] == "999.99"
