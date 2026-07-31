import pytest

from ragspine.core.llm import LLMError, LLMResult
from ragspine.rag import selfrag
from ragspine.rag.retrieval import Hit
from ragspine.web import websearch

_DDG_HTML = b"""
<div class="results">
<div class="result results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="https://example.com/one">Example One</a>
    </h2>
    <a class="result__snippet" href="https://example.com/one">Snippet about <b>pdv</b> stope.</a>
  </div>
</div>
<div class="result results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="https://example.com/two">Example Two</a>
    </h2>
    <a class="result__snippet" href="https://example.com/two">Druga snippet.</a>
  </div>
</div>
</div>
"""


class _FakeLLM:
    def __init__(self, text=None, raise_error=False):
        self._text = text
        self._raise = raise_error

    def complete(self, messages, system=None, **kw):
        if self._raise:
            raise LLMError("boom")
        return LLMResult(text=self._text, model="m", usage={})


@pytest.mark.parametrize("query,expected", [
    ("koliki je pdv?", "simple"),
    ("bok", "simple"),
    ("x" * 121, "complex"),
    ("moram platiti pdv i predati prijavu i zatvoriti mjesec", "complex"),
    ("je li ovo tocno? a ovo? treca provjera?", "complex"),
])
def test_classify(query, expected):
    assert selfrag.classify(query) == expected


def test_k_for():
    assert selfrag.k_for("koliki je pdv?") == 8
    assert selfrag.k_for("x" * 121) == 16


def _hits():
    return [Hit(chunk_id=1, doc_id=1, title="t", text="tekst", score=1.0, doc_type="zakon")]


def test_check_relevance_no_llm():
    assert selfrag.check_relevance(None, "q", _hits()) is True


def test_check_relevance_ne():
    assert selfrag.check_relevance(_FakeLLM(text="NE"), "q", _hits()) is False


def test_check_relevance_da():
    assert selfrag.check_relevance(_FakeLLM(text="DA"), "q", _hits()) is True


def test_check_relevance_fail_open_on_error():
    assert selfrag.check_relevance(_FakeLLM(raise_error=True), "q", _hits()) is True


def test_ddg_parses_results():
    results = websearch.ddg("pdv stopa", fetch=lambda url, **kw: _DDG_HTML)
    assert len(results) == 2
    assert results[0]["title"] == "Example One"
    assert results[0]["url"] == "https://example.com/one"
    assert "pdv" in results[0]["snippet"]
    assert results[1]["url"] == "https://example.com/two"


def test_ddg_parse_error_returns_empty():
    assert websearch.ddg("q", fetch=lambda url, **kw: b"not even html \xff\xfe") == []


def test_websearch_handle_no_llm(spine, cfg):
    def fake(url, **kw):
        return _DDG_HTML

    result = websearch.handle(spine, cfg, "pdv stopa", None, fetch=fake)
    assert "Example One" in result and "https://example.com/one" in result
