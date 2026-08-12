"""Source watching: fetch, hash-diff, law diff, rate/date extraction,
config overrides, upcoming changes, notifications."""
import hashlib, html, json, re, sqlite3
from dataclasses import dataclass
from xml.etree import ElementTree

from atlas.core import xmlsafe
from atlas.core.net import safe_fetch
from atlas.docs.ingest import ingest_text
from atlas.web.learn import HR_GRADOVI as CITIES

_SURTAX_MAX_PCT = 30.0  # HR historical max ~18% (Zagreb); anything above = bogus/attack

INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "ugostiteljstvo": ["ugostitelj", "restoran", "kafic", "hrana", "pice", "turisticka pristojba"],
    "gradevina": ["gradevin", "gradnja", "izvodac", "projektiranje", "nekretnin"],
    "trgovina": ["trgovina", "maloprodaja", "veleprodaja", "prodavaonica", "roba"],
    "prijevoz": ["prijevoz", "transport", "logistika", "vozila", "cestarina"],
    "poljoprivreda": ["poljoprivreda", "ratarstvo", "stocarstvo", "subvencije", "opg"],
    "it": ["informatika", "softver", "programiranje", "it usluge", "digitalne usluge"],
    "turizam": ["turizam", "smjestaj", "apartman", "hotel", "najam"],
    "proizvodnja": ["proizvodnja", "industrija", "tvornica", "pogon", "prerada"],
}

# Narodne novine does not offer a classic RSS feed -- it publishes through HTML/RDF/JSON-LD
# (verified 2026-08-01: there is no rss.aspx endpoint). Hence there are no default rss sources;
# the official/international/announcements sections are tracked as 'page' hash-diff sources (see
# ops/seeds.py NN_LISTINGS). Industry-keyword RSS matching (check_rss) remains
# available for any real RSS that a worker adds through /watchlist/sources kind='rss'.
# ponytail: if NN ever introduces RSS, add it here and seeds.watch_defaults will pick it up.
DEFAULT_RSS: list[tuple[str, str]] = []

_DIACRITICS = str.maketrans("čćžšđ", "cczsd")


def _normalize(text: str) -> str:
    # NFKD + strip combining marks: decomposed Unicode ("c" + U+030C) must
    # match the same as the precomposed "c-caron"
    import unicodedata
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))
    return text.lower().translate(_DIACRITICS)

_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_EFFECTIVE_RE = re.compile(r"stupa na snagu\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\.?", re.I)


@dataclass
class Change:
    source_id: int
    url: str
    summary: str
    diff: list[str]


def _strip_html(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def law_diff(old: str, new: str) -> list[str]:
    """Sentence-level diff; changed pairs formatted as 'old → new' so both
    old and new numbers are visible in a single entry."""
    import difflib, itertools

    old_lines, new_lines = _sentences(old), _sentences(new)
    diff: list[str] = []
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for o, n in itertools.zip_longest(old_lines[i1:i2], new_lines[j1:j2], fillvalue=""):
            if o == n:
                continue
            diff.append(f"{o} → {n}" if o and n else (o or n))
    return diff


def _gap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """Character distance between two spans; 0 if they touch/overlap."""
    if a_end <= b_start:
        return b_start - a_end
    if b_end <= a_start:
        return a_start - b_end
    return 0


def extract_rates(text: str) -> dict[str, str]:
    """City → percentage, pairing each city with the '%' number that follows
    it in reading order (one-city-one-rate, e.g. "Grad ... X%"), capped to a
    sane distance. Sentence-boundary splitting alone isn't enough: real pages
    often separate city/rate rows with newlines or tabs instead of periods,
    or with no separator at all beyond a single space — so this walks city
    and percentage matches together in text order and binds each percentage
    to the nearest preceding, not-yet-bound city, rather than trusting
    leftmost-in-a-window or sentence membership."""
    tokens = [(m.start(), m.end(), "city", city)
              for city in CITIES for m in re.finditer(re.escape(city), text)]
    tokens += [(m.start(), m.end(), "pct", m.group(1)) for m in _RATE_RE.finditer(text)]
    tokens.sort(key=lambda t: t[0])

    rates: dict[str, str] = {}
    pending: tuple[int, int, str] | None = None  # (start, end, city) awaiting a rate
    for start, end, kind, val in tokens:
        if kind == "city":
            pending = (start, end, val)
        elif pending is not None and _gap(pending[0], pending[1], start, end) <= 40:
            rates[pending[2]] = val.replace(",", ".")
            pending = None
    return rates


def extract_effective_dates(text: str) -> list[tuple[str, str]]:
    """(sentence, ISO date) pairs from 'stupa na snagu d.m.yyyy.' phrases."""
    out = []
    for m in _EFFECTIVE_RE.finditer(text):
        d, mo, y = m.groups()
        iso = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        start = text.rfind(".", 0, m.start()) + 1
        end = text.find(".", m.end())
        end = end + 1 if end != -1 else len(text)
        out.append((text[start:end].strip(), iso))
    return out


def add_source(spine, url, category="", client_id=None, added_by="", kind="page") -> int:
    with spine.write() as c:
        try:
            return c.execute(
                "INSERT INTO watch_sources(url,category,client_id,added_by,kind) VALUES(?,?,?,?,?)",
                (url, category, client_id, added_by, kind),
            ).lastrowid
        except sqlite3.IntegrityError:
            return c.execute("SELECT id FROM watch_sources WHERE url=?", (url,)).fetchone()["id"]


def get_source(spine, sid):
    return spine.read().execute("SELECT * FROM watch_sources WHERE id=?", (sid,)).fetchone()


def check_source(spine, cfg, source_row, fetch=None) -> Change | None:
    fetch = fetch or safe_fetch
    text = _strip_html(fetch(source_row["url"]))
    new_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    state = spine.read().execute(
        "SELECT * FROM watch_state WHERE source_id=?", (source_row["id"],)
    ).fetchone()

    if state is None:  # baseline — first ever fetch, nothing to diff against
        with spine.write() as c:
            c.execute(
                "INSERT INTO watch_state(source_id,last_hash,last_checked,last_content) "
                "VALUES(?,?,datetime('now'),?)",
                (source_row["id"], new_hash, text),
            )
            c.execute("INSERT INTO law_versions(source_id,content) VALUES(?,?)", (source_row["id"], text))
        return None

    if state["last_hash"] == new_hash:
        with spine.write() as c:
            c.execute("UPDATE watch_state SET last_checked=datetime('now') WHERE source_id=?", (source_row["id"],))
        return None

    diff = law_diff(state["last_content"] or "", text)
    for city, rate in extract_rates(text).items():
        # common-sense bound: the surtax in HR is 0-~18%; a malicious/MITM'd source
        # must not live-set e.g. 999% into the calculator. Out of bounds -> skip.
        try:
            if not 0 <= float(rate) <= _SURTAX_MAX_PCT:
                continue
        except ValueError:
            continue
        spine.set_override("kalkulator", f"prirez.{city}", rate, source_row["url"])
    dates = extract_effective_dates(text)
    cat = (source_row["category"] or "").strip()
    summary = f"[{cat}] Promjena na {source_row['url']}" if cat else f"Promjena na {source_row['url']}"

    prior = spine.read().execute(
        "SELECT id FROM documents WHERE source_url=? AND status='active'", (source_row["url"],)
    ).fetchone()

    new_doc_id = ingest_text(spine, text, title=source_row["url"], source_url=source_row["url"],
                              client_id=source_row["client_id"])

    if prior is not None and new_doc_id is not None:
        try:
            from atlas.rag import versioning
            versioning.supersede(spine, prior["id"], new_doc_id)
        except Exception:
            # best-effort: never let versioning break the watch run
            try:
                with spine.write() as c:
                    c.execute("UPDATE documents SET status='superseded' WHERE id=?", (prior["id"],))
            except Exception:
                pass

    # G: a hit on the office's own keyword in the CHANGE (diff, not the whole
    # text) -> a separate, louder notification
    hits = match_keywords(spine, " ".join(diff) if diff else text)

    with spine.write() as c:
        for desc, iso in dates:
            c.execute(
                "INSERT INTO upcoming_changes(source_id,description,effective_date) VALUES(?,?,?)",
                (source_row["id"], desc, iso),
            )
        c.execute(
            "INSERT INTO notifications(kind,body,client_id) VALUES(?,?,?)",
            ("law_change", summary, source_row["client_id"]),
        )
        if hits:
            c.execute(
                "INSERT INTO notifications(kind,body,client_id) VALUES(?,?,?)",
                ("keyword_hit",
                 f"Ključne riječi ({', '.join(hits)}) u promjeni: {source_row['url']}",
                 source_row["client_id"]),
            )
        c.execute(
            "UPDATE watch_state SET last_hash=?, last_checked=datetime('now'), last_content=? WHERE source_id=?",
            (new_hash, text, source_row["id"]),
        )
        c.execute("INSERT INTO law_versions(source_id,content) VALUES(?,?)", (source_row["id"], text))

    return Change(source_id=source_row["id"], url=source_row["url"], summary=summary, diff=diff)


def check_all(spine, cfg, fetch=None) -> list[Change]:
    """Check every active source. A single source's fetch/parse failure is
    isolated (recorded as a watch_error notification) so it can't abort the
    run for every other source."""
    rows = spine.read().execute("SELECT * FROM watch_sources WHERE active=1").fetchall()
    changes = []
    for row in rows:
        try:
            ch = check_source(spine, cfg, row, fetch=fetch)
        except Exception as e:
            with spine.write() as c:
                c.execute(
                    "INSERT INTO notifications(kind,body,client_id) VALUES(?,?,?)",
                    ("watch_error", f"{row['url']}: {e}", row["client_id"]),
                )
            continue
        if ch:
            changes.append(ch)
    return changes


def parse_rss(xml_bytes: bytes) -> list[dict]:
    """Parse an RSS 2.0 feed (<channel><item>...) into title/link/description/
    date dicts. Malformed XML or missing channel/item structure -> []."""
    try:
        root = xmlsafe.fromstring(xml_bytes)
    except (ElementTree.ParseError, xmlsafe.XmlBlocked):
        return []
    items = []
    for item in root.iter("item"):
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "date": (item.findtext("pubDate") or "").strip(),
        })
    return items


def check_rss(spine, cfg, fetch=None) -> list[dict]:
    """Check every active RSS source (kind='rss'). Only items whose link
    hasn't been seen before are candidates; a candidate is relevant if its
    text matches an industry keyword AND at least one active client is in
    that industry. Relevant items get a notification and are returned.
    Per-source try/except isolation, like check_all."""
    fetch = fetch or safe_fetch
    rows = spine.read().execute(
        "SELECT * FROM watch_sources WHERE kind='rss' AND active=1"
    ).fetchall()
    client_industries = {
        r["industry"] for r in spine.read().execute(
            "SELECT DISTINCT industry FROM clients WHERE active=1"
        ).fetchall() if r["industry"]
    }

    relevant: list[dict] = []
    for row in rows:
        try:
            items = parse_rss(fetch(row["url"]))
            state = spine.read().execute(
                "SELECT * FROM watch_state WHERE source_id=?", (row["id"],)
            ).fetchone()
            seen = set(json.loads(state["last_content"])) if state and state["last_content"] else set()
            new_items = [i for i in items if i["link"] not in seen]
            seen |= {i["link"] for i in items}

            with spine.write() as c:
                if state is None:
                    c.execute(
                        "INSERT INTO watch_state(source_id,last_hash,last_checked,last_content) "
                        "VALUES(?,?,datetime('now'),?)",
                        (row["id"], "", json.dumps(sorted(seen))),
                    )
                else:
                    c.execute(
                        "UPDATE watch_state SET last_checked=datetime('now'), last_content=? WHERE source_id=?",
                        (json.dumps(sorted(seen)), row["id"]),
                    )

                for item in new_items:
                    text = _normalize(item["title"] + " " + item["description"])
                    hit_industries = {
                        ind for ind, keywords in INDUSTRY_KEYWORDS.items()
                        if any(_normalize(kw) in text for kw in keywords)
                    } & client_industries
                    if not hit_industries:
                        continue

                    client = c.execute(
                        f"SELECT id FROM clients WHERE active=1 AND industry IN "
                        f"({','.join('?' * len(hit_industries))}) LIMIT 1",
                        tuple(hit_industries),
                    ).fetchone()
                    c.execute(
                        "INSERT INTO notifications(kind,body,client_id) VALUES(?,?,?)",
                        ("rss", f"{item['title']} {item['link']}", client["id"] if client else None),
                    )
                    relevant.append(item)
        except Exception as e:
            with spine.write() as c:
                c.execute(
                    "INSERT INTO notifications(kind,body,client_id) VALUES(?,?,?)",
                    ("watch_error", f"{row['url']}: {e}", row["client_id"]),
                )
            continue

    return relevant


def mark_stale(spine) -> int:
    with spine.write() as c:
        cur = c.execute(
            "UPDATE documents SET stale=1 WHERE valid_until IS NOT NULL AND valid_until < date('now') AND stale=0"
        )
        return cur.rowcount


# --- G: the office's own keywords (data-driven, not hardcoded) ----------------

def get_keywords(spine) -> list[str]:
    raw = spine.get_override("watchlist", "keywords", "")
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        data = []
    return [w for w in data if isinstance(w, str)]


def set_keywords(spine, words, user: str = "?") -> list[str]:
    out, seen = [], set()
    for w in words or []:
        if not isinstance(w, str):
            raise ValueError("ključna riječ mora biti string")
        w = w.strip()[:60]
        if not w:
            continue
        key = _normalize(w)
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) > 100:
            raise ValueError("previše ključnih riječi (max 100)")
    spine.set_override("watchlist", "keywords", json.dumps(out, ensure_ascii=False))
    spine.audit(user, "watch_keywords", json.dumps(out, ensure_ascii=False))
    return out


def match_keywords(spine, text: str) -> list[str]:
    t = _normalize(text or "")
    return [w for w in get_keywords(spine) if _normalize(w) in t]


# --- G: Excel export of the watchlist (openpyxl is an optional [full] dependency) ---

_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n")


def _cell(v):
    """Anti formula-injection: a value from watched pages that starts
    with = + - @ (or a leading TAB/CR/LF -- OWASP CSV-injection) could become
    an EXECUTABLE formula -- the ' prefix neutralizes it."""
    if isinstance(v, str) and v[:1] in _FORMULA_LEAD:
        return "'" + v
    return v


def export_xlsx(spine) -> bytes:
    from atlas.core import optional as _optional
    openpyxl = _optional.need("openpyxl", "Excel izvoz")
    if openpyxl is None:
        raise ValueError("openpyxl nije instaliran (pip install atlas[full])")
    import io

    from atlas.business import deadline_calendar
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Nadolazeće promjene"
    ws.append(["Stupa na snagu", "Opis", "Izvor"])
    for r in spine.read().execute(
            """SELECT u.effective_date, u.description, s.url
               FROM upcoming_changes u LEFT JOIN watch_sources s ON s.id=u.source_id
               ORDER BY u.effective_date""").fetchall():
        ws.append([_cell(r["effective_date"]), _cell(r["description"]), _cell(r["url"])])

    ws2 = wb.create_sheet("Rokovi")
    ws2.append(["Rok", "Vrsta", "Opis"])
    for r in deadline_calendar.upcoming(spine, days=60):
        ws2.append([_cell(r["due"]), _cell(r["kind"]), _cell(r["description"])])

    ws3 = wb.create_sheet("Izvori")
    ws3.append(["URL", "Kategorija", "Vrsta", "Aktivan"])
    for r in spine.read().execute(
            "SELECT url, category, kind, active FROM watch_sources ORDER BY url").fetchall():
        ws3.append([_cell(r["url"]), _cell(r["category"]), _cell(r["kind"]),
                    "da" if r["active"] else "ne"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
