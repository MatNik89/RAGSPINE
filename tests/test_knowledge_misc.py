import pytest

from atlas.core.llm import LLMError, LLMResult
from atlas.knowledge import features, patterns, translate
from atlas.web.api import create_app
from atlas.web.deps import add_user
from fastapi.testclient import TestClient


class _FakeLLM:
    def __init__(self, text=None, raise_error=False):
        self._text = text
        self._raise = raise_error

    def complete(self, messages, system=None, **kw):
        if self._raise:
            raise LLMError("boom")
        return LLMResult(text=self._text, model="m", usage={})


# --- translate ---

def test_translate_calls_llm():
    assert translate.translate(_FakeLLM(text="Hello"), "Bok", "en") == "Hello"


def test_translate_bad_lang_raises():
    with pytest.raises(ValueError):
        translate.translate(_FakeLLM(text="x"), "x", "xx")


def test_translate_no_llm_raises():
    with pytest.raises(ValueError):
        translate.translate(None, "x", "en")


# --- features ---

def test_features_add_and_list(spine):
    fid = features.add(spine, "ana", "treba mi export u Excel")
    rows = features.list_open(spine)
    assert any(r["id"] == fid and r["body"] == "treba mi export u Excel" for r in rows)


def test_features_priority_order(spine):
    features.add(spine, "ana", "nisko", priority=3)
    high_id = features.add(spine, "ana", "hitno", priority=1)
    rows = features.list_open(spine)
    assert rows[0]["id"] == high_id


# --- patterns ---

def test_normalize_collapses_digits():
    assert patterns.normalize("Top 5 klijenata") == patterns.normalize("top 10 klijenata")
    assert patterns.normalize("koliki je prirez za Split") != patterns.normalize(
        "koliki je prirez za Zadar")


def test_detect_finds_repeated_pattern(spine):
    for n in (5, 10, 3, 7, 20, 1):
        with spine.write() as c:
            c.execute(
                "INSERT INTO interactions(user, query, lane) VALUES (?,?,?)",
                ("ana", f"top {n} klijenata", "sql"),
            )
    groups = patterns.detect(spine, min_count=5)
    assert any(g["count"] >= 5 for g in groups)
    rows = spine.read().execute("SELECT * FROM skill_suggestions").fetchall()
    assert len(rows) == 1
    assert rows[0]["count"] >= 5


def test_detect_below_min_count_no_suggestion(spine):
    with spine.write() as c:
        c.execute(
            "INSERT INTO interactions(user, query, lane) VALUES (?,?,?)",
            ("ana", "koliko je 2+2", "chat"),
        )
    groups = patterns.detect(spine, min_count=5)
    assert groups == []
    rows = spine.read().execute("SELECT * FROM skill_suggestions").fetchall()
    assert rows == []


# --- API ---

def _client(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "ana", "tajna")
    tok = c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def test_api_features_add_and_list(spine, cfg):
    c = _client(spine, cfg)
    r = c.post("/features", json={"body": "želim dark mode"})
    assert r.status_code == 200
    fid = r.json()["id"]
    r2 = c.get("/features")
    assert r2.status_code == 200
    assert any(row["id"] == fid for row in r2.json())


def test_api_patterns(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/patterns")
    assert r.status_code == 200
    assert r.json() == []


def test_api_translate_bad_lang_400(spine, cfg, monkeypatch):
    c = _client(spine, cfg)
    from atlas.web import api as api_mod
    monkeypatch.setattr(api_mod, "LLMClient", lambda cfg: _FakeLLM(text="Hello"))
    r = c.post("/translate", json={"text": "Bok", "target": "xx"})
    assert r.status_code == 400


def test_api_translate_llm_error_503_scrubbed(spine, cfg, monkeypatch):
    # LLMError body must not leak provider internals ("boom") to the client.
    # /translate builds its LLM via model_settings.build_llm -> mock THAT layer
    # (mocking api.LLMClient is ineffective and lets a real OAuth LLM answer).
    c = _client(spine, cfg)
    monkeypatch.setattr("atlas.business.model_settings.build_llm",
                        lambda spine, cfg: _FakeLLM(raise_error=True))
    r = c.post("/translate", json={"text": "Bok", "target": "en"})
    assert r.status_code == 503
    assert "boom" not in r.json()["detail"]
