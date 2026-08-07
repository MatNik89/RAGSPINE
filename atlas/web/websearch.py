"""DuckDuckGo web-search lane: used as fallback when retrieval is irrelevant.

ponytail: DDG's HTML is unofficial and its class names can change under us —
ceiling is "scrape until it breaks". Upgrade path: official search API (Bing/
Brave) or a self-hosted SearXNG JSON endpoint, swapped in behind ddg()'s
signature.
"""
from html.parser import HTMLParser
from urllib.parse import quote

from atlas.core.llm import LLMError, LLMUnavailable
from atlas.core.net import safe_fetch
from atlas.rag import budget, composer
from atlas.rag.retrieval import Hit

_DDG_URL = "https://html.duckduckgo.com/html/?q={}"


class _DDGParser(HTMLParser):
    """Pulls (title, url, snippet) out of DDG's result markup.

    Tracks same-tag nesting depth per field so a title/snippet element that
    itself contains inline tags (e.g. <b>) doesn't close early.
    """

    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self._cur = None
        self._field = None
        self._field_tag = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if self._field is None:
            if "result__a" in classes:
                self._cur = {"title": "", "url": dict(attrs).get("href", ""), "snippet": ""}
                self._field, self._field_tag, self._depth = "title", tag, 1
            elif "result__snippet" in classes and self._cur is not None:
                self._field, self._field_tag, self._depth = "snippet", tag, 1
        elif tag == self._field_tag:
            self._depth += 1

    def handle_data(self, data):
        if self._field and self._cur is not None:
            self._cur[self._field] += data

    def handle_endtag(self, tag):
        if self._field and tag == self._field_tag:
            self._depth -= 1
            if self._depth == 0:
                if self._field == "snippet":
                    self.results.append(self._cur)
                    self._cur = None
                self._field = None
                self._field_tag = None


def ddg(query: str, fetch=None) -> list[dict]:
    fetch = fetch or safe_fetch
    url = _DDG_URL.format(quote(query))
    try:
        data = fetch(url)
        parser = _DDGParser()
        parser.feed(data.decode("utf-8", errors="replace"))
    except Exception:
        return []
    return [r for r in parser.results if r.get("title")][:10]


def handle(spine, cfg, query: str, llm, fetch=None) -> str:
    results = ddg(query, fetch=fetch)
    if not results:
        # None = ugovor lane handlera: pipeline nastavlja na chat/LLM put.
        # (E2E nalaz: string ovdje je svaki upit na praznom indeksu pretvarao
        # u mrtvo "Nema web rezultata." bez ikakvog LLM odgovora.)
        return None

    listing = "\n".join(f"{r['title']} — {r['url']}" for r in results)
    if llm is None:
        return listing

    hits = budget.compact(
        [Hit(chunk_id=0, doc_id=0, title=r["title"], text=r["snippet"], score=1.0, doc_type="web")
         for r in results])
    extra = "Izvori su s weba [WEB] (DuckDuckGo), s adresama:\n" + "\n".join(
        f"- {r['title']}: {r['url']}" for r in results)
    system, messages = composer.compose(query, hits, extra=extra)
    try:
        result = llm.complete(messages, system=system)
    except (LLMError, LLMUnavailable):
        return listing
    return result.text


from atlas.rag import pipeline  # noqa: E402  (lazy: avoid any import-order coupling)
pipeline.LANE_HANDLERS["web"] = handle
