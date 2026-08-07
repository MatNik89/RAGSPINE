from atlas.ops import model_recommender as mr


def test_classify_tier_buckets():
    assert mr.classify_tier(4) == "tiny"
    assert mr.classify_tier(12) == "small"
    assert mr.classify_tier(24) == "medium"
    assert mr.classify_tier(48) == "large"
    assert mr.classify_tier(96) == "dgx"


def test_classify_tier_boundaries():
    assert mr.classify_tier(8) == "small"
    assert mr.classify_tier(16) == "medium"
    assert mr.classify_tier(32) == "large"
    assert mr.classify_tier(64) == "dgx"


def test_recommend_medium_ram_only():
    rec = mr.recommend({"ram_gb": 16, "gpu": None})
    assert rec["tier"] == "medium"
    assert rec["total_gb"] == 16
    assert "model" in rec["roles"]["chat"]
    assert "model" in rec["roles"]["embed"]


def test_recommend_tiny_chat_is_warn():
    rec = mr.recommend({"ram_gb": 4, "gpu": None})
    assert rec["tier"] == "tiny"
    assert "model" not in rec["roles"]["chat"]
    assert "warn" in rec["roles"]["chat"]
    assert "model" in rec["roles"]["embed"]  # embedding still feasible


def test_recommend_parses_gpu_vram():
    rec = mr.recommend({"ram_gb": 16, "gpu": "NVIDIA RTX 3060 12GB"})
    assert rec["total_gb"] == 28
    assert rec["tier"] == "medium"


def test_recommend_gpu_unparseable_treated_as_zero():
    rec = mr.recommend({"ram_gb": 16, "gpu": "some mystery GPU"})
    assert rec["total_gb"] == 16
    assert rec["tier"] == "medium"


def test_recommend_shape():
    rec = mr.recommend({"ram_gb": 16, "gpu": None})
    assert set(rec) == {"tier", "total_gb", "roles", "ollama_installed", "already_pulled"}
    assert isinstance(rec["ollama_installed"], bool)
    assert isinstance(rec["already_pulled"], list)


def test_litellm_config_contains_expected_keys():
    rec = mr.recommend({"ram_gb": 16, "gpu": None})
    yaml_text = mr.litellm_config(rec)
    assert "model_list:" in yaml_text
    assert "http://127.0.0.1:11434" in yaml_text
    assert rec["roles"]["chat"]["model"] in yaml_text


def test_pull_commands_deduped():
    rec = mr.recommend({"ram_gb": 16, "gpu": None})
    cmds = mr.pull_commands(rec)
    assert all(c.startswith("ollama pull ") for c in cmds)
    assert len(cmds) == len(set(cmds))
    # every non-warn model shows up exactly once
    models = [r["model"] for r in rec["roles"].values() if "model" in r]
    assert len(cmds) == len(set(models))


def test_report_contains_tier_and_model():
    text = mr.report({"ram_gb": 16, "gpu": None})
    rec = mr.recommend({"ram_gb": 16, "gpu": None})
    assert isinstance(text, str)
    assert rec["tier"] in text
    assert rec["roles"]["chat"]["model"] in text


def test_api_models_recommend(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user

    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.get("/models/recommend", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "tier" in r.json()


def test_api_models_litellm(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user

    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    r = c.get("/models/litellm", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "model_list:" in r.text
