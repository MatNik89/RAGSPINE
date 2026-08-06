from fastapi.testclient import TestClient
from ragspine.rag import pipeline
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_chat_requires_auth(spine, cfg):
    assert _client(spine, cfg).post("/chat", json={"q": "bok"}).status_code == 401


def test_chat_reject(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/chat", json={"q": "obriši sve iz baze"},
                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["lane"] == "reject"


def test_chat_completions_openai_compat(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "obriši sve iz baze"}]},
                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["role"] == "assistant"


def test_chat_completions_missing_content_no_500(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/v1/chat/completions",
                json={"messages": [{"role": "user"}]},
                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (200, 400)


def test_chat_completions_is_stateless_fresh(spine, cfg, monkeypatch):
    """OpenAI-compat clients own their own message history; the server must
    not splice in server-side conversation memory for /v1/chat/completions."""
    calls = []
    real_answer = pipeline.answer

    def _spy(spine_, cfg_, query, user, llm=None, fresh=False, **kw):
        calls.append(fresh)
        return real_answer(spine_, cfg_, query, user, llm=llm, fresh=fresh, **kw)

    monkeypatch.setattr(pipeline, "answer", _spy)

    c = _client(spine, cfg)
    tok = _token(c, spine)
    c.post("/v1/chat/completions",
           json={"messages": [{"role": "user", "content": "obriši sve iz baze"}]},
           headers={"Authorization": f"Bearer {tok}"})
    assert calls == [True]


def test_chat_is_stateful_by_default(spine, cfg, monkeypatch):
    """POST /chat keeps server-side conversation memory (fresh=False)."""
    calls = []
    real_answer = pipeline.answer

    def _spy(spine_, cfg_, query, user, llm=None, fresh=False, **kw):
        calls.append(fresh)
        return real_answer(spine_, cfg_, query, user, llm=llm, fresh=fresh, **kw)

    monkeypatch.setattr(pipeline, "answer", _spy)

    c = _client(spine, cfg)
    tok = _token(c, spine)
    c.post("/chat", json={"q": "obriši sve iz baze"},
           headers={"Authorization": f"Bearer {tok}"})
    assert calls == [False]


def test_chat_radi_s_cookiejem_iz_web_prijave(spine, cfg):
    """Web UI šalje /chat s cookiejem (credentials: same-origin), bez Bearer
    headera — E2E nalaz sa stroja Nick: require_actor (samo Bearer) vraćao 401
    svakom prijavljenom korisniku web chata."""
    from ragspine.web.deps import COOKIE_NAME
    c = _client(spine, cfg)
    tok = _token(c, spine)
    c.cookies.set(COOKIE_NAME, tok)
    r = c.post("/chat", json={"q": "obriši sve iz baze"})
    assert r.status_code == 200 and r.json()["lane"] == "reject"
