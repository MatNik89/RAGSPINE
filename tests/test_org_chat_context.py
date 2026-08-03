"""Faza C spojnog tkiva: wiki/skills/memorija ulaze u chat prompt; L0 zapis; distill job."""
from ragspine.business.acl import Actor
from ragspine.core.llm import LLMClient
from ragspine.docs import ingest
from ragspine.knowledge import memory_layers, skills
from ragspine.ops import jobs
from ragspine.rag import pipeline

ACTOR = Actor(user_id=1, org_id=1, role="member", username="ana")


def _llm_capture(cfg, text):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    captured = []

    def transport(url, headers, body):
        captured.append(body.get("messages", []))
        return {"choices": [{"message": {"content": text}}], "model": "m", "usage": {}}

    return LLMClient(cfg, transport=transport), captured


def _seed_doc(spine, org_id=1):
    ingest.ingest_text(spine, "Rok za JOPPD obrazac je 15. u mjesecu.", "JOPPD rok",
                       doc_type="zakon", org_id=org_id)


def test_chat_prompt_includes_skill_and_memory(spine, cfg):
    _seed_doc(spine)
    sid = skills.create_skill(spine, 1, "JOPPD predaja", trigger="joppd",
                              steps="1. Otvori ePoreznu\n2. Učitaj obrazac")
    skills.set_status(spine, sid, "active")
    with spine.write() as c:
        c.execute("INSERT INTO mem_l1(org_id,user_id,kind,content) VALUES(1,1,'preference',"
                  "'Korisnik preferira kratke odgovore o JOPPD obrascu')")
    llm, cap = _llm_capture(cfg, "Rok je 15. u mjesecu [1].")
    pipeline.answer(spine, cfg, "kako predati joppd obrazac?", "ana", llm=llm, actor=ACTOR)
    sent = "\n".join(m["content"] for m in cap[-1])
    assert "Interni postupak 'JOPPD predaja'" in sent
    assert "kratke odgovore" in sent


def test_chat_context_is_org_scoped(spine, cfg):
    _seed_doc(spine, org_id=2)
    sid = skills.create_skill(spine, 1, "JOPPD predaja", trigger="joppd", steps="tajni koraci org 1")
    skills.set_status(spine, sid, "active")
    other = Actor(user_id=9, org_id=2, role="member", username="tudja")
    llm, cap = _llm_capture(cfg, "Rok je 15. u mjesecu [1].")
    pipeline.answer(spine, cfg, "kako predati joppd obrazac?", "tudja", llm=llm, actor=other)
    sent = "\n".join(m["content"] for m in cap[-1])
    assert "tajni koraci org 1" not in sent


def test_chat_records_l0_turns(spine, cfg):
    _seed_doc(spine)
    llm, _ = _llm_capture(cfg, "Rok je 15. u mjesecu [1].")
    pipeline.answer(spine, cfg, "koji je rok za joppd obrazac?", "ana", llm=llm, actor=ACTOR)
    rows = spine.read().execute(
        "SELECT role FROM mem_l0 WHERE org_id=1 AND user_id=1 ORDER BY id").fetchall()
    assert [r["role"] for r in rows] == ["user", "assistant"]


def test_no_actor_no_l0_no_crash(spine, cfg):
    _seed_doc(spine)
    llm, _ = _llm_capture(cfg, "Rok je 15. u mjesecu [1].")
    pipeline.answer(spine, cfg, "koji je rok za joppd obrazac?", "ana", llm=llm)
    assert spine.read().execute("SELECT COUNT(*) AS n FROM mem_l0").fetchone()["n"] == 0


def test_memory_distill_job_distills(spine, cfg, monkeypatch):
    memory_layers.record_turn(spine, 1, 1, "ana", "user", "Radim place za klijenta Alfa")
    calls = []
    monkeypatch.setattr(jobs, "LLMClient", None, raising=False)

    def fake_distill(sp, org, uid, llm):
        calls.append((org, uid)); return {"atoms": 0}

    from ragspine.knowledge import memory_layers as ml
    monkeypatch.setattr(ml, "distill", fake_distill)
    jobs.memory_distill_job(spine, cfg)
    assert calls == [(1, 1)]
