from ragspine.business.dnevnice import RATES
from ragspine.core.spine import init_spine
from ragspine.ops import seeds, setup


def test_seeds_all_counts(spine):
    counts = seeds.all(spine, 2026)
    assert counts["kontni_plan"] >= 40
    assert counts["watch"] >= 4
    assert counts["quickref"] == 24
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


def test_llmfit_absent_returns_none(cfg, monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    assert setup.llmfit(cfg) is None


def test_detect_providers_shape_and_no_secret_leak(cfg, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("RAGSPINE_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")
    monkeypatch.setattr(setup, "_ollama_alive", lambda cfg: False)

    result = setup.detect_providers(cfg)
    assert set(result) == {"env_keys", "oauth", "ollama_models"}
    assert result["env_keys"] == ["OPENAI_API_KEY"]
    assert result["ollama_models"] == []
    dumped = str(result)
    assert "sk-super-secret-value" not in dumped


def test_run_returns_report_string(tmp_path, cfg, monkeypatch):
    init_spine(cfg.db_path)
    monkeypatch.setattr(setup, "_ollama_alive", lambda cfg: False)
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    report = setup.run(cfg)
    assert isinstance(report, str)
    assert "kontni plan" in report.lower()
    assert "hardver" in report.lower()
