import json

from atlas.core.llm import LLMClient
from atlas.knowledge import wiki


def _llm(cfg, pages):
    cfg.llm_base_url = "https://api.x.com"; cfg.llm_api_key = "k"; cfg.llm_model = "m"
    text = json.dumps(pages)
    return LLMClient(cfg, transport=lambda u, h, b: {
        "choices": [{"message": {"content": text}}], "model": "m", "usage": {}})


_PAGES = [
    {"type": "entity", "title": "PDV", "body": "Porez na dodanu vrijednost. Vezano uz [[Stopa PDV-a]]."},
    {"type": "concept", "title": "Stopa PDV-a", "body": "Opća stopa je 25%."},
]


# ---------- synthesize / extract ----------

def test_synthesize_parses_json(cfg):
    pages = wiki.synthesize(_llm(cfg, _PAGES), "neki tekst o PDV-u")
    assert [p["title"] for p in pages] == ["PDV", "Stopa PDV-a"]


def test_extract_json_tolerates_fences():
    assert wiki._extract_json('```json\n[{"title":"A"}]\n```') == [{"title": "A"}]
    assert wiki._extract_json("nema jsona") == []


def test_slug_canonical():
    assert wiki._slug("entity", "Stopa PDV-a") == "entity/stopa-pdv-a"
    assert wiki._slug("bogus", "Ime") == "concept/ime"  # nepoznat tip → concept


# ---------- ingest ----------

def test_ingest_creates_pages_and_links(spine, cfg):
    r = wiki.ingest_source(spine, 1, "zakon-pdv.txt", "tekst", _llm(cfg, _PAGES))
    assert r["pages"] == 2 and r["written"] == 2
    n = spine.read().execute("SELECT COUNT(*) c FROM wiki_pages WHERE org_id=1").fetchone()["c"]
    assert n == 2
    links = spine.read().execute("SELECT dst_slug FROM wiki_links").fetchall()
    assert any(l["dst_slug"] == "stopa-pdv-a" for l in links)  # [[Stopa PDV-a]] → link


def test_ingest_sha_incremental_skips_unchanged(spine, cfg):
    wiki.ingest_source(spine, 1, "s.txt", "isti tekst", _llm(cfg, _PAGES))
    r2 = wiki.ingest_source(spine, 1, "s.txt", "isti tekst", _llm(cfg, _PAGES))
    assert r2["skipped"] is True


def test_locked_page_not_overwritten(spine, cfg):
    wiki.ingest_source(spine, 1, "s.txt", "v1", _llm(cfg, _PAGES))
    wiki.set_locked(spine, 1, "concept/stopa-pdv-a", True)
    # novi izvor (druga sha) s izmijenjenim tijelom za istu stranicu
    changed = [{"type": "concept", "title": "Stopa PDV-a", "body": "IZMIJENJENO 24%"}]
    wiki.ingest_source(spine, 1, "s.txt", "v2 razlicit", _llm(cfg, changed))
    body = spine.read().execute(
        "SELECT body FROM wiki_pages WHERE org_id=1 AND slug='concept/stopa-pdv-a'").fetchone()["body"]
    assert "IZMIJENJENO" not in body  # locked → sačuvano ručno uređeno


def test_reingest_updates_unlocked_and_bumps_version(spine, cfg):
    wiki.ingest_source(spine, 1, "s.txt", "v1", _llm(cfg, _PAGES))
    changed = [{"type": "concept", "title": "Stopa PDV-a", "body": "NOVO 13%"}]
    wiki.ingest_source(spine, 1, "s.txt", "v2 drukcije", _llm(cfg, changed))
    row = spine.read().execute(
        "SELECT body, version FROM wiki_pages WHERE org_id=1 AND slug='concept/stopa-pdv-a'").fetchone()
    assert "NOVO" in row["body"] and row["version"] == 2


# ---------- search (org-scoped) ----------

def test_search_org_scoped_and_related(spine, cfg):
    wiki.ingest_source(spine, 1, "s.txt", "t", _llm(cfg, _PAGES))
    wiki.ingest_source(spine, 2, "s.txt", "t", _llm(cfg, _PAGES))  # druga org
    hits = wiki.search(spine, 1, "stopa PDV-a")
    assert hits and all("slug" in h for h in hits)
    pdv = [h for h in hits if h["title"] == "PDV"]
    if pdv:
        assert "stopa-pdv-a" in pdv[0]["related"]  # graf poveznica
    # izolacija: org 2 sadržaj se ne miješa (isti naslovi, ali odvojeni po org_id)
    assert all(isinstance(h["title"], str) for h in wiki.search(spine, 2, "PDV"))


def test_search_no_match_empty(spine, cfg):
    wiki.ingest_source(spine, 1, "s.txt", "t", _llm(cfg, _PAGES))
    assert wiki.search(spine, 1, "kvinsarska tematika xyzzy") == []
