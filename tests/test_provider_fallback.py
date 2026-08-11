"""Routing-lanac s fallbackom: primarni + fallback provideri; na grešku prelazi
na sljedeći. Ključevi šifrirani; endpoint admin-only."""
import pytest

from atlas.business import model_settings as ms
from atlas.core.llm import FallbackLLM, LLMError, LLMResult, LLMUnavailable, ProviderHealthTracker


class _FakeClient:
    def __init__(self, fail=False, tag="x"):
        self.fail, self.tag = fail, tag

    def supports_tools(self):
        return True

    def complete(self, *a, **k):
        if self.fail:
            raise LLMUnavailable("pao")
        return LLMResult(text=self.tag, model=self.tag, usage={}, tool_calls=[])


def test_fallback_uses_second_when_first_fails():
    f = FallbackLLM([None, None])
    f._clients = [_FakeClient(fail=True), _FakeClient(fail=False, tag="drugi")]
    f._keys = [f"p{i}" for i in range(len(f._clients))]
    f._health = ProviderHealthTracker()
    res = f.complete([{"role": "user", "content": "x"}])
    assert res.text == "drugi" and f.last_used == 1


def test_fallback_first_wins_when_ok():
    f = FallbackLLM([None, None])
    f._clients = [_FakeClient(fail=False, tag="prvi"), _FakeClient(fail=False, tag="drugi")]
    f._keys = [f"p{i}" for i in range(len(f._clients))]
    f._health = ProviderHealthTracker()
    assert f.complete([]).text == "prvi" and f.last_used == 0


def test_fallback_all_fail_raises_last():
    f = FallbackLLM([None])
    f._clients = [_FakeClient(fail=True), _FakeClient(fail=True)]
    f._keys = [f"p{i}" for i in range(len(f._clients))]
    f._health = ProviderHealthTracker()
    with pytest.raises(LLMUnavailable):
        f.complete([])


def test_set_and_get_fallbacks_masks_key(spine, cfg):
    ms.set_fallbacks(spine, [
        {"provider": "ollama", "model": "llama3", "ollama_url": "http://127.0.0.1:11434"},
        {"provider": "deepseek", "model": "deepseek-chat",
         "base_url": "https://api.deepseek.com", "api_key": "TAJNI123"},
    ], cfg)
    ui = ms.get_fallbacks(spine)
    assert [p["provider"] for p in ui] == ["ollama", "deepseek"]
    assert ui[1]["has_api_key"] is True and "TAJNI123" not in str(ui)  # ključ maskiran


def test_fallbacks_key_encrypted_at_rest(spine, cfg):
    ms.set_fallbacks(spine, [{"provider": "deepseek", "base_url": "https://api.deepseek.com",
                              "api_key": "TAJNI123"}], cfg)
    raw = spine.get_override("model", "fallbacks", "")
    assert "TAJNI123" not in raw  # šifriran u bazi
    dec = ms.fallbacks(spine, cfg)
    assert dec[0]["api_key"] == "TAJNI123"  # dešifrira se za lanac


def test_chain_is_primary_plus_fallbacks(spine, cfg):
    ms.save(spine, "ollama", model="prim", ollama_url="http://127.0.0.1:11434")
    ms.set_fallbacks(spine, [{"provider": "ollama", "model": "fb1",
                              "ollama_url": "http://127.0.0.1:11434"}], cfg)
    ch = ms.chain(spine, cfg)
    assert len(ch) == 2 and ch[0].llm_model == "prim" and ch[1].llm_model == "fb1"


def test_set_fallbacks_rejects_unknown_provider(spine, cfg):
    with pytest.raises(ValueError):
        ms.set_fallbacks(spine, [{"provider": "izmisljeno"}], cfg)


def test_endpoint_admin_only(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from atlas.business import tenancy
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw"); complete_setup(spine)
    ho = {"Authorization": "Bearer " + c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]}
    add_user(spine, "m", "pw")
    tm = c.post("/auth/login", json={"username": "m", "password": "pw"}).json()["token"]
    tenancy.add_member(spine, tenancy.default_org_id(spine),
                       spine.read().execute("SELECT id FROM users WHERE username='m'").fetchone()["id"], "member")
    body = {"profiles": [{"provider": "ollama", "model": "x", "ollama_url": "http://127.0.0.1:11434"}]}
    assert c.post("/model/fallbacks", headers={"Authorization": f"Bearer {tm}"}, json=body).status_code == 403
    assert c.post("/model/fallbacks", headers=ho, json=body).status_code == 200
    assert c.get("/model/fallbacks", headers=ho).json()["fallbacks"][0]["provider"] == "ollama"


def test_masked_key_preserved_on_resave(spine, cfg):
    # GET maskira ključ; ponovni POST bez ključa (prazan) NE smije obrisati stari
    ms.set_fallbacks(spine, [{"provider": "deepseek", "base_url": "https://api.deepseek.com",
                              "api_key": "TAJNI123"}], cfg)
    ms.set_fallbacks(spine, [{"provider": "deepseek", "base_url": "https://api.deepseek.com",
                              "api_key": ""}], cfg)  # read-edit-write, ključ maskiran
    assert ms.fallbacks(spine, cfg)[0]["api_key"] == "TAJNI123"


def test_fallbacks_count_capped(spine, cfg):
    many = [{"provider": "ollama", "ollama_url": "http://127.0.0.1:11434"} for _ in range(6)]
    with pytest.raises(ValueError):
        ms.set_fallbacks(spine, many, cfg)


def test_primary_key_encrypted_at_rest_with_cfg(spine, cfg):
    ms.save(spine, "anthropic", model="claude", api_key="PRIMARN0", cfg=cfg)
    raw = spine.get_override("model", "api_key", "")
    assert "PRIMARN0" not in raw and raw.startswith("enc:")  # šifriran u bazi
    assert ms.apply(spine, cfg).llm_api_key == "PRIMARN0"     # apply dešifrira


def test_primary_key_plaintext_backcompat_without_cfg(spine, cfg):
    ms.save(spine, "anthropic", model="claude", api_key="STAR1")  # bez cfg = plaintext
    assert ms.apply(spine, cfg).llm_api_key == "STAR1"  # apply svejedno radi (fallback)


def test_fallback_logs_and_survives_oserror(monkeypatch):
    class OSErrClient:
        def supports_tools(self): return True
        def complete(self, *a, **k): raise OSError("timeout")
    f = FallbackLLM([None, None])
    f._clients = [OSErrClient(), _FakeClient(fail=False, tag="drugi")]
    f._keys = [f"p{i}" for i in range(len(f._clients))]
    f._health = ProviderHealthTracker()
    assert f.complete([]).text == "drugi"  # OSError na primarnom -> ide na sljedeći


# ---------- Provider health-cooldown (MateClaw obrazac) ----------

def test_tracker_parks_after_threshold_and_expires():
    t = [0]
    h = ProviderHealthTracker(threshold=3, cooldown_ms=1000, clock=lambda: t[0])
    for _ in range(2):
        h.record_failure("p")
    assert h.is_in_cooldown("p") is False        # 2 < prag 3
    h.record_failure("p")
    assert h.is_in_cooldown("p") is True          # 3. -> parkiran
    t[0] = 999
    assert h.is_in_cooldown("p") is True
    t[0] = 1000
    assert h.is_in_cooldown("p") is False          # istekao -> očišćen


def test_tracker_retry_after_parks_immediately_and_never_shortens():
    t = [0]
    h = ProviderHealthTracker(threshold=3, cooldown_ms=1000, clock=lambda: t[0])
    h.record_failure("p", retry_after_ms=5000)     # provider rekao 5s -> odmah park
    assert h.is_in_cooldown("p") is True
    h.record_failure("p", retry_after_ms=1000)     # kraći ne smije skratiti
    t[0] = 1500
    assert h.is_in_cooldown("p") is True            # još u 5s prozoru


def test_tracker_success_clears():
    h = ProviderHealthTracker(threshold=1, cooldown_ms=1000, clock=lambda: 0)
    h.record_failure("p")
    assert h.is_in_cooldown("p") is True
    h.record_success("p")
    assert h.is_in_cooldown("p") is False


def test_fallback_skips_cooled_primary():
    h = ProviderHealthTracker(threshold=1, cooldown_ms=10_000, clock=lambda: 0)
    h.record_failure("p0")                          # primarni parkiran
    f = FallbackLLM([None, None], health=h)
    f._keys = ["p0", "p1"]
    f._clients = [_FakeClient(fail=True), _FakeClient(fail=False, tag="drugi")]
    assert f.complete([]).text == "drugi" and f.last_used == 1  # primarni preskočen


def test_fallback_all_cooled_last_resort_still_tries():
    h = ProviderHealthTracker(threshold=1, cooldown_ms=10_000, clock=lambda: 0)
    h.record_failure("p0"); h.record_failure("p1")   # oba parkirana
    f = FallbackLLM([None, None], health=h)
    f._keys = ["p0", "p1"]
    f._clients = [_FakeClient(fail=True), _FakeClient(fail=False, tag="zadnja")]
    assert f.complete([]).text == "zadnja"           # svi cooled -> zadnja šansa svejedno proba


def test_parse_retry_after_seconds_and_clamp():
    from atlas.core.llm import _parse_retry_after
    assert _parse_retry_after("30") == 30_000
    assert _parse_retry_after("0") == 1000            # clamp donji
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("smeće") is None


def test_client_error_400_does_not_park_provider():
    # request-specifičan 400 (loš upit) NE smije parkirati zdravog primarnog (Codex)
    from atlas.core.llm import _counts_health
    e400 = LLMError("bad request"); e400.status = 400
    assert _counts_health(e400) is False
    e429 = LLMError("rate"); e429.status = 429
    assert _counts_health(e429) is True
    assert _counts_health(LLMUnavailable("x")) is True
    assert _counts_health(OSError("timeout")) is True


def test_fallback_400_on_primary_still_fails_over_but_no_park():
    h = ProviderHealthTracker(threshold=1, cooldown_ms=10_000, clock=lambda: 0)
    f = FallbackLLM([None, None], health=h)
    f._keys = ["p0", "p1"]

    class _C400:
        def supports_tools(self): return True
        def complete(self, **k):
            e = LLMError("bad"); e.status = 400; raise e
    f._clients = [_C400(), _FakeClient(fail=False, tag="ok")]
    assert f.complete([]).text == "ok"
    assert h.is_in_cooldown("p0") is False  # 400 nije parkirao primarnog


def test_provider_key_differs_by_apikey():
    import types
    from atlas.core.llm import _provider_key
    a = types.SimpleNamespace(llm_provider="openai", llm_base_url="u", llm_path="", llm_model="m", llm_api_key="k1", ollama_url="")
    b = types.SimpleNamespace(llm_provider="openai", llm_base_url="u", llm_path="", llm_model="m", llm_api_key="k2", ollama_url="")
    assert _provider_key(a) != _provider_key(b) and "k1" not in _provider_key(a)
