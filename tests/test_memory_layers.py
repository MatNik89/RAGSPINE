import json

from atlas.core.llm import LLMClient
from atlas.knowledge import memory_layers as ml


def _llm(cfg, payload):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return LLMClient(cfg, transport=lambda u, h, b: {
        "choices": [{"message": {"content": text}}], "model": "m", "usage": {}})


_ATOMS = [
    {"kind": "preference", "content": "Ne diraj stari auth modul jer ga mobitel još koristi."},
    {"kind": "fact", "content": "Klijent Bistro Adria je mjesečni PDV obveznik."},
]


def test_record_and_distill_atoms(spine, cfg):
    ml.record_turn(spine, 1, 10, "s1", "user", "Nemoj dirati stari auth, mobitel ga koristi.")
    ml.record_turn(spine, 1, 10, "s1", "assistant", "Ok, neću.")
    r = ml.distill(spine, 1, 10, _llm(cfg, _ATOMS))
    assert r["atoms"] == 2
    # L0 označen destiliranim → drugi distill bez novih replika = 0
    assert ml.distill(spine, 1, 10, _llm(cfg, _ATOMS))["atoms"] == 0


def test_distill_dedups_similar_atoms(spine, cfg):
    ml.record_turn(spine, 1, 10, "s1", "user", "a")
    ml.distill(spine, 1, 10, _llm(cfg, _ATOMS))
    ml.record_turn(spine, 1, 10, "s2", "user", "b")
    # skoro isti atom + jedan novi
    dup = [{"kind": "preference", "content": "Ne diraj stari auth modul, mobitel ga koristi."},
           {"kind": "fact", "content": "Rok za JOPPD je 15. u mjesecu."}]
    r = ml.distill(spine, 1, 10, _llm(cfg, dup))
    assert r["atoms"] == 1  # duplikat preskočen, samo novi JOPPD atom
    total = spine.read().execute("SELECT COUNT(*) c FROM mem_l1 WHERE org_id=1 AND user_id=10").fetchone()["c"]
    assert total == 3


def test_build_persona(spine, cfg):
    ml.record_turn(spine, 1, 10, "s1", "user", "x")
    ml.distill(spine, 1, 10, _llm(cfg, _ATOMS))
    persona = ml.build_persona(spine, 1, 10, _llm(cfg, "PROFIL: oprezan s legacy kodom."))
    assert persona and "PROFIL" in persona
    row = spine.read().execute("SELECT persona FROM mem_l3 WHERE org_id=1 AND user_id=10").fetchone()
    assert "PROFIL" in row["persona"]


def test_recall_layered_and_budgeted(spine, cfg):
    ml.record_turn(spine, 1, 10, "s1", "user", "x")
    ml.distill(spine, 1, 10, _llm(cfg, _ATOMS))
    ml.build_persona(spine, 1, 10, _llm(cfg, "profil teksta"))
    out = ml.recall(spine, 1, 10, "koji je PDV status Bistro Adria?")
    assert "profil" in out["persona"]
    assert any("Bistro Adria" in a for a in out["atoms"])
    # budžet: max_items ograničava
    tight = ml.recall(spine, 1, 10, "auth modul mobitel", max_items=1)
    assert len(tight["atoms"]) <= 1


def test_org_and_user_isolation(spine, cfg):
    ml.record_turn(spine, 1, 10, "s", "user", "x")
    ml.distill(spine, 1, 10, _llm(cfg, _ATOMS))
    # druga org / drugi user ne vide atome
    assert ml.recall(spine, 2, 10, "Bistro Adria")["atoms"] == []
    assert ml.recall(spine, 1, 11, "Bistro Adria")["atoms"] == []
