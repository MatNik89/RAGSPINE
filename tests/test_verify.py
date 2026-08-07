from atlas.core.llm import LLMClient
from atlas.docs import ingest as ing
from atlas.rag import pipeline, verify


def _llm(cfg, text):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    return LLMClient(cfg, transport=lambda u, h, b: {
        "choices": [{"message": {"content": text}}], "model": "m", "usage": {}})


# ---------- prag odbijanja (80%) ----------

def test_accepted_answer_shows_tocnost(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "Zakon o PDV-u", doc_type="zakon")
    r = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana",
                        llm=_llm(cfg, "Stopa je 25% [1]."))
    assert "Točnost:" in r["answer"] and r["confidence"] >= 0.80
    assert r["sources"]


def test_below_threshold_refuses_with_explanation(spine, cfg):
    # slab izvor (default autoritet) + polovična pokrivenost -> < 80% -> odbija
    ing.ingest_text(spine, "Pravilo o pausalnom obrtu je zapisano ovdje.", "Bilješka",
                    doc_type="ostalo")
    r = pipeline.answer(spine, cfg, "koje je pravilo o pausalnom obrtu?", "ana",
                        llm=_llm(cfg, "Prva tvrdnja [1]. Druga tvrdnja bez izvora."))
    assert 0 < r["confidence"] < 0.80          # ima izvor, ali preslab/nepotpun
    assert "nisam dovoljno siguran" in r["answer"].lower()
    assert "točnost" in r["answer"].lower()
    assert r["sources"] == []  # ne nudi izvore za odgovor koji odbija


def test_empty_retrieval_says_idk_not_yesman(spine, cfg):
    # nema pronađenog izvora -> ne smije samo prepričati model (yes-man), nego IDK
    ing.ingest_text(spine, "Nešto sasvim nevezano.", "X", doc_type="ostalo")
    r = pipeline.answer(spine, cfg, "koliki je porez na neki izmišljeni pojam xyzzy?", "ana",
                        llm=_llm(cfg, "Naravno, iznos je 42% [1]."))
    assert "ne znam" in r["answer"].lower() and r["confidence"] == 0


def test_no_citation_still_idk(spine, cfg):
    ing.ingest_text(spine, "Stopa PDV-a je 25 posto.", "Zakon o PDV-u", doc_type="zakon")
    r = pipeline.answer(spine, cfg, "kolika je stopa pdv-a?", "ana",
                        llm=_llm(cfg, "Izmišljam bez ijednog citata."))
    assert "ne znam" in r["answer"].lower() and r["confidence"] == 0


# ---------- iterativna petlja ----------

def test_run_is_bounded_and_records_passes(spine, cfg, monkeypatch):
    from atlas.rag.retrieval import Hit
    hit = Hit(1, 1, "Bilješka", "tekst", 1.0, "ostalo")
    monkeypatch.setattr("atlas.rag.verify.retrieval.search", lambda *a, **k: [hit])
    best = verify.run(spine, "pitanje", [hit], _llm(cfg, "bez citata."),
                      threshold=0.80, max_passes=3)
    assert best["passes"] <= 3           # ograničeno
    assert best["confidence"] == 0.0     # nema citata -> 0
    assert not verify.accepted(best)


def test_run_stops_at_first_pass_when_confident(spine, cfg, monkeypatch):
    from atlas.rag.retrieval import Hit
    calls = {"n": 0}

    def _search(*a, **k):
        calls["n"] += 1
        return [Hit(1, 1, "Zakon o PDV-u", "Stopa je 25%.", 1.0, "zakon")]
    monkeypatch.setattr("atlas.rag.verify.retrieval.search", _search)
    hit = Hit(1, 1, "Zakon o PDV-u", "Stopa je 25%.", 1.0, "zakon")
    best = verify.run(spine, "stopa pdv", [hit], _llm(cfg, "25% [1]."))
    assert best["passes"] == 1 and calls["n"] == 0  # nije trebao proširivati


def test_reformulate_adds_title_terms():
    from atlas.rag.retrieval import Hit
    hits = [Hit(1, 1, "Zakon o porezu na dohodak", "x", 1.0, "zakon")]
    q = verify._reformulate("koja je stopa", hits)
    assert "dohodak" in q and q.startswith("koja je stopa")


def test_merge_dedupes_by_chunk_id():
    from atlas.rag.retrieval import Hit
    a = [Hit(1, 1, "A", "x", 1.0, "zakon")]
    b = [Hit(1, 1, "A", "x", 1.0, "zakon"), Hit(2, 2, "B", "y", 1.0, "zakon")]
    merged = verify._merge(a, b)
    assert [h.chunk_id for h in merged] == [1, 2]


# ---------- anti-yes-man prompt ----------

def test_composer_prompt_is_not_yes_man():
    from atlas.rag import composer
    assert "yes-man" in composer.SYSTEM.lower()
    assert "ospori" in composer.SYSTEM.lower()


def test_composer_prompt_deprivileges_sources():
    # izvori su podaci, ne naredbe (anti prompt-injection kroz ingestirani sadržaj)
    from atlas.rag import composer
    s = composer.SYSTEM.lower()
    assert "podatak" in s and "naredb" in s
