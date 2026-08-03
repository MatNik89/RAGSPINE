"""TIER 2 ostatak: čišćenje šuma, dedup-ljestvica, kompakcija konteksta,
rate-limit, audit admin-gate, verify org-ekspanzija."""
from fastapi.testclient import TestClient

from ragspine.docs import ingest
from ragspine.rag import budget, verify
from ragspine.rag.retrieval import Hit
from ragspine.web.api import create_app
from ragspine.web.deps import add_user
from ragspine.web.ratelimit import RateLimiter


# --- čišćenje šuma ---

def test_clean_noise_drops_page_numbers_and_repeated_boilerplate():
    page = "Knjigovodstvo d.o.o. — interni dokument\nSadržaj stranice {n} o PDV-u.\nStranica {n}/3\n"
    text = "".join(page.replace("{n}", str(i)) for i in (1, 2, 3))
    cleaned = ingest.clean_noise(text)
    assert cleaned.count("Knjigovodstvo d.o.o.") == 1        # header samo prvi put
    assert "Stranica 1/3" not in cleaned and "Stranica 3/3" not in cleaned
    for i in (1, 2, 3):
        assert f"Sadržaj stranice {i}" in cleaned            # sadržaj netaknut


def test_clean_noise_keeps_normal_text():
    text = "Zakon o PDV-u.\n\nStopa je 25 posto.\nStopa je 13 posto za neke usluge."
    assert ingest.clean_noise(text) == text


# --- dedup-ljestvica ---

def test_ingest_dedups_identical_chunks_within_doc(spine):
    para = "Ovo je identičan odlomak koji se ponavlja u dokumentu. " * 20
    text = f"{para}\n\nJedinstveni odlomak o JOPPD obrascu.\n\n{para}"
    doc_id = ingest.ingest_text(spine, text, "dupli")
    chunks = [r["text"] for r in spine.read().execute(
        "SELECT text FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
    assert len(chunks) == len({ingest._norm_sha(c) for c in chunks})


# --- kompakcija ---

def _hit(i, text):
    return Hit(i, i, f"dok{i}", text, 1.0 / i, "zakon")


def test_compact_tiers_full_then_truncated_then_dropped():
    hits = [_hit(i, "riječ " * 800) for i in range(1, 31)]  # ~4800 znakova svaki
    out = budget.compact(hits, budget_tokens=3000)
    assert 0 < len(out) < len(hits)                          # rep ispao
    assert out[0].text == hits[0].text                       # tier 1 pun
    assert out[-1].text.endswith("…")                        # tier 2 skraćen
    assert [h.chunk_id for h in out] == [h.chunk_id for h in hits[:len(out)]]  # prefiks-red


def test_compact_small_context_untouched():
    hits = [_hit(1, "kratko"), _hit(2, "isto kratko")]
    assert budget.compact(hits) == hits


def test_est_tokens_monotone():
    assert budget.est_tokens("") <= budget.est_tokens("riječ") < budget.est_tokens("riječ " * 50)


# --- rate-limit ---

def test_ratelimiter_window():
    rl = RateLimiter()
    assert all(rl.allow("k", 3) for _ in range(3))
    assert not rl.allow("k", 3)
    assert rl.allow("drugi", 3)                              # neovisan ključ


def test_login_rate_limited(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    for _ in range(10):
        c.post("/auth/login", json={"username": "ana", "password": "kriva"})
    r = c.post("/auth/login", json={"username": "ana", "password": "tajna"})
    assert r.status_code == 429


def test_chat_rate_limited(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    for _ in range(30):
        c.post("/chat", json={"q": "obriši sve"}, headers=h)  # reject lane, jeftino
    assert c.post("/chat", json={"q": "bok"}, headers=h).status_code == 429


# --- audit admin-gate ---

def test_audit_admin_only(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "pw")
    owner = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    add_user(spine, "boris", "pw2")
    member = c.post("/auth/login", json={"username": "boris", "password": "pw2"}).json()["token"]
    assert c.get("/audit", headers={"Authorization": f"Bearer {member}"}).status_code == 403
    assert c.get("/audit", headers={"Authorization": f"Bearer {owner}"}).status_code == 200


# --- verify ekspanzija nosi org_id ---

def test_verify_expansion_respects_org(spine, cfg, monkeypatch):
    calls = []
    real_search = verify.retrieval.search

    def spy(sp, q, k=8, freshness=True, org_id=None):
        calls.append(org_id)
        return real_search(sp, q, k=k, freshness=freshness, org_id=org_id)

    monkeypatch.setattr(verify.retrieval, "search", spy)
    ingest.ingest_text(spine, "Rok za JOPPD je 15. u mjesecu, prema pravilniku.",
                       "JOPPD", doc_type="zakon", org_id=7)
    hits = real_search(spine, "rok joppd", org_id=7)

    class _LLM:  # niska pouzdanost → forsira ekspanzijski prolaz
        def complete(self, messages, system=None):
            class R: text = "Možda je 15. [1]. Nisam siguran."
            return R()

    verify.run(spine, "rok joppd", hits, _LLM(), org_id=7, threshold=0.99)
    assert calls and all(o == 7 for o in calls)
