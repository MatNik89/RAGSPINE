import pytest

from atlas.docs import ingest as ing
from atlas.rag import cache, conversation, pipeline
from atlas.core.llm import LLMClient


def _insert(spine, user, query, answer):
    with spine.write() as c:
        c.execute(
            "INSERT INTO interactions(user,query,lane,answer,confidence) VALUES(?,?,?,?,?)",
            (user, query, "chat", answer, 1.0),
        )


def test_recent_turns_chronological_and_per_user(spine):
    _insert(spine, "ana", "q1", "a1")
    _insert(spine, "ana", "q2", "a2")
    _insert(spine, "ana", "q3", "a3")
    _insert(spine, "ivan", "qx", "ax")

    turns = conversation.recent_turns(spine, "ana", 5)

    assert [t["query"] for t in turns] == ["q1", "q2", "q3"]
    assert [t["answer"] for t in turns] == ["a1", "a2", "a3"]
    assert all(t["query"] != "qx" for t in turns)


def test_recent_turns_respects_limit(spine):
    for i in range(7):
        _insert(spine, "ana", f"q{i}", f"a{i}")
    turns = conversation.recent_turns(spine, "ana", 5)
    assert len(turns) == 5
    assert [t["query"] for t in turns] == ["q2", "q3", "q4", "q5", "q6"]


def test_as_messages_alternates_and_truncates():
    turns = [
        {"query": "koliki je prirez za Split?", "answer": "x" * 900},
        {"query": "a za Zadar?", "answer": "kratko"},
    ]
    msgs = conversation.as_messages(turns)
    assert len(msgs) == 4
    assert msgs[0] == {"role": "user", "content": "koliki je prirez za Split?"}
    assert msgs[1]["role"] == "assistant"
    assert len(msgs[1]["content"]) <= 500
    assert msgs[2] == {"role": "user", "content": "a za Zadar?"}
    assert msgs[3] == {"role": "assistant", "content": "kratko"}


def _llm_capture(cfg, text):
    """LLM whose transport records the messages list it received."""
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    captured = []

    def transport(url, headers, body):
        captured.append(body.get("messages", []))
        return {"choices": [{"message": {"content": text}}], "model": "m", "usage": {}}

    return LLMClient(cfg, transport=transport), captured


def test_pipeline_multi_turn_includes_prior_context(spine, cfg):
    ing.ingest_text(spine, "Prirez u Splitu je 15 posto.", "prirez", doc_type="zakon")
    ing.ingest_text(spine, "Prirez u Zadru je 10 posto.", "prirez", doc_type="zakon")

    llm1, cap1 = _llm_capture(cfg, "Prirez u Splitu je 15% [1].")
    pipeline.answer(spine, cfg, "koliki je prirez za Split?", "ana", llm=llm1)

    llm2, cap2 = _llm_capture(cfg, "Prirez u Zadru je 10% [1].")
    pipeline.answer(spine, cfg, "a za Zadar?", "ana", llm=llm2)

    sent = cap2[-1]
    contents = [m["content"] for m in sent]
    assert any("koliki je prirez za Split?" in c for c in contents)
    assert any("Prirez u Splitu je 15% [1]." in c for c in contents)
    # current-turn question must come after the prior turns
    idx_prior = next(i for i, c in enumerate(contents) if "koliki je prirez za Split?" in c)
    idx_current = next(i for i, c in enumerate(contents) if "a za Zadar?" in c)
    assert idx_prior < idx_current


def test_pipeline_multi_turn_isolated_per_user(spine, cfg):
    ing.ingest_text(spine, "Prirez u Splitu je 15 posto.", "prirez", doc_type="zakon")

    llm1, _ = _llm_capture(cfg, "Prirez u Splitu je 15% [1].")
    pipeline.answer(spine, cfg, "koliki je prirez za Split?", "ana", llm=llm1)

    llm2, cap2 = _llm_capture(cfg, "Ne znam bez konteksta.")
    pipeline.answer(spine, cfg, "a za Zadar?", "boris", llm=llm2)

    sent = cap2[-1]
    contents = [m["content"] for m in sent]
    assert not any("koliki je prirez za Split?" in c for c in contents)


def test_pipeline_survives_history_lookup_failure(spine, cfg, monkeypatch):
    ing.ingest_text(spine, "Prirez u Splitu je 15 posto.", "prirez", doc_type="zakon")

    def _boom(*a, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(conversation, "recent_turns", _boom)

    llm, _ = _llm_capture(cfg, "Prirez u Splitu je 15% [1].")
    r = pipeline.answer(spine, cfg, "koliki je prirez za Split?", "ana", llm=llm)
    assert r["lane"] == "chat" and r["confidence"] > 0


def test_pipeline_cache_bypassed_once_user_has_history(spine, cfg):
    """query_cache is text-keyed. A short elliptical follow-up like "a za
    Zadar?" can repeat verbatim across unrelated conversations; once a user
    has prior turns, that text must NOT short-circuit through some earlier
    conversation's cached answer for the same text (that would silently
    skip history+citations and surface a stale, wrong-context reply). Only a
    user's genuinely context-free first turn is safe to serve from cache."""
    cache.put(spine, "a za Zadar?", "STALE ODGOVOR IZ DRUGOG KONTEKSTA")
    _insert(spine, "ana", "koliki je prirez za Split?", "Prirez u Splitu je 15%.")

    llm2, cap2 = _llm_capture(cfg, "Odgovor iz trenutnog konteksta.")
    r2 = pipeline.answer(spine, cfg, "a za Zadar?", "ana", llm=llm2)

    assert r2["cached"] is False
    assert r2["answer"] != "STALE ODGOVOR IZ DRUGOG KONTEKSTA"
    assert cap2  # LLM was actually called, not served from the stale cache entry
    # prior turn's Q&A must be present as history ahead of the current message
    contents = [m["content"] for m in cap2[-1]]
    assert "koliki je prirez za Split?" in contents[:-1]


def test_pipeline_fresh_flag_skips_history(spine, cfg):
    ing.ingest_text(spine, "Prirez u Splitu je 15 posto.", "prirez", doc_type="zakon")

    llm1, _ = _llm_capture(cfg, "Prirez u Splitu je 15% [1].")
    pipeline.answer(spine, cfg, "koliki je prirez za Split?", "ana", llm=llm1)

    llm2, cap2 = _llm_capture(cfg, "Ne znam bez konteksta.")
    pipeline.answer(spine, cfg, "a za Zadar?", "ana", llm=llm2, fresh=True)

    sent = cap2[-1]
    contents = [m["content"] for m in sent]
    assert not any("koliki je prirez za Split?" in c for c in contents)
