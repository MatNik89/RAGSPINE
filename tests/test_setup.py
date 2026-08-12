from atlas.business.per_diem import RATES
from atlas.ops import seeds, setup


def test_seeds_all_counts(spine):
    counts = seeds.all(spine, 2026)
    assert counts["kontni_plan"] >= 40
    assert counts["watch"] >= 4
    from atlas.business import quickref
    assert counts["quickref"] == len(quickref.SEED)
    assert counts["kalendar"] > 20
    assert counts["dnevnice"] == len(RATES)


def test_seeds_all_idempotent(spine):
    seeds.all(spine, 2026)
    total_1 = {
        "kontni_plan": spine.read().execute("SELECT COUNT(*) c FROM kontni_plan").fetchone()["c"],
        "watch": spine.read().execute("SELECT COUNT(*) c FROM watch_sources").fetchone()["c"],
        "quickref": spine.read().execute("SELECT COUNT(*) c FROM quickref").fetchone()["c"],
        "dnevnice": spine.read().execute("SELECT COUNT(*) c FROM dnevnice_rates").fetchone()["c"],
    }
    second = seeds.all(spine, 2026)
    assert second["kontni_plan"] == 0
    assert second["watch"] == 0
    assert second["quickref"] == 0
    assert second["dnevnice"] == 0
    total_2 = {
        "kontni_plan": spine.read().execute("SELECT COUNT(*) c FROM kontni_plan").fetchone()["c"],
        "watch": spine.read().execute("SELECT COUNT(*) c FROM watch_sources").fetchone()["c"],
        "quickref": spine.read().execute("SELECT COUNT(*) c FROM quickref").fetchone()["c"],
        "dnevnice": spine.read().execute("SELECT COUNT(*) c FROM dnevnice_rates").fetchone()["c"],
    }
    assert total_1 == total_2


def test_detect_hw_shape():
    hw = setup.detect_hw()
    assert set(hw) == {"cpu_cores", "ram_gb", "disk_free_gb", "gpu", "apple_silicon"}
    assert hw["cpu_cores"] >= 1
    assert hw["ram_gb"] >= 0
    assert hw["disk_free_gb"] > 0
    assert isinstance(hw["apple_silicon"], bool)


