"""Learn lane: fetch a URL, pull city surtax rates out of it, store them as
config overrides, and ingest the page as a document."""
import re
from html.parser import HTMLParser

from ragspine.core.net import safe_fetch
from ragspine.docs.ingest import ingest_text

# ponytail: duplicates watchlist.CITIES (different 29-city list, same shape).
# Kept separate rather than making watchlist import from here to avoid a
# cross-module coupling neither side needs yet; upgrade path if they ever
# must agree exactly = hoist one shared list both modules import.
HR_GRADOVI = [
    "Zagreb", "Split", "Rijeka", "Osijek", "Zadar", "Pula", "Sisak", "Karlovac",
    "Varaždin", "Šibenik", "Dubrovnik", "Slavonski Brod", "Vinkovci", "Vukovar",
    "Bjelovar", "Koprivnica", "Križevci", "Đakovo", "Požega", "Čakovec",
    "Virovitica", "Samobor", "Velika Gorica", "Kaštela", "Solin", "Metković",
    "Makarska",
]

_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_URL_RE = re.compile(r"https?://\S+")


class _TextExtractor(HTMLParser):
    """Collects text nodes, dropping anything inside <script>/<style>."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def clean_html(html_bytes: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(html_bytes.decode("utf-8", errors="replace"))
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def extract_city_rates(text: str) -> dict[str, str]:
    """City -> percentage, pairing each city with the next '%' number within
    an 80-char window (same order-pairing idea as watchlist.extract_rates)."""
    tokens = [(m.start(), m.end(), "city", city)
              for city in HR_GRADOVI for m in re.finditer(re.escape(city), text)]
    tokens += [(m.start(), m.end(), "pct", m.group(1)) for m in _RATE_RE.finditer(text)]
    tokens.sort(key=lambda t: t[0])

    rates: dict[str, str] = {}
    pending: tuple[int, int, str] | None = None
    for start, end, kind, val in tokens:
        if kind == "city":
            pending = (start, end, val)
        elif pending is not None and start - pending[1] <= 80:
            rates[pending[2]] = val.replace(",", ".")
            pending = None
    return rates


def learn_url(spine, cfg, url: str, user: str, fetch=None) -> dict:
    fetch = fetch or safe_fetch
    text = clean_html(fetch(url))
    overrides = extract_city_rates(text)
    for city, rate in overrides.items():
        spine.set_override("kalkulator", f"prirez.{city}", rate, url)
    doc_id = ingest_text(spine, text, title=f"learn:{url}", source_url=url)
    summary = f"Naučeno s {url}: {len(overrides)} prireza, doc_id={doc_id}"
    spine.audit(user, "learn_url", url, summary)
    return {"url": url, "overrides_set": overrides, "doc_id": doc_id}


def handle(spine, cfg, query: str, llm, fetch=None) -> str:
    m = _URL_RE.search(query)
    if not m:
        return "Pošalji URL, npr: nauči s https://..."
    result = learn_url(spine, cfg, m.group(0), "sustav", fetch=fetch)
    if not result["overrides_set"]:
        return f"Naučeno s {result['url']}: stranica spremljena, brojke nisu prepoznate."
    parts = ", ".join(f"{c} ({r})" for c, r in result["overrides_set"].items())
    return f"Naučeno s {result['url']}: postavljeni prirezi za {parts}. Stranica spremljena."


from ragspine.rag import pipeline  # noqa: E402  (lazy: avoid any import-order coupling)
pipeline.LANE_HANDLERS["learn"] = handle
