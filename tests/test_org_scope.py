"""Faza B spojnog tkiva: org-scope retrievala, cachea, kb-a i backfill migracija."""
from ragspine.business import tenancy
from ragspine.docs import ingest
from ragspine.knowledge import kb
from ragspine.rag import cache, retrieval


def _org(spine, name):
    with spine.write() as c:
        return c.execute("INSERT INTO orgs(name) VALUES(?)", (name,)).lastrowid


def test_retrieval_hard_org_filter(spine):
    o1, o2 = _org(spine, "A"), _org(spine, "B")
    ingest.ingest_text(spine, "PDV stopa je 25 posto.", "PDV pravilo", org_id=o1)
    assert retrieval.search(spine, "pdv stopa", org_id=o1)
    assert retrieval.search(spine, "pdv stopa", org_id=o2) == []
    # bez org_id (legacy/CLI) — globalni pogled i dalje radi
    assert retrieval.search(spine, "pdv stopa")


def test_ingest_stamps_default_org_when_none(spine):
    ingest.ingest_text(spine, "Rok za JOPPD je 15. u mjesecu.", "JOPPD rok")
    row = spine.read().execute("SELECT org_id FROM documents").fetchone()
    assert row["org_id"] == tenancy.default_org_id(spine)


def test_backfill_stamps_legacy_rows(spine):
    with spine.write() as c:
        c.execute("INSERT INTO documents(title, sha256) VALUES('stari', 'x1')")
        c.execute("INSERT INTO knowledge(question, answer) VALUES('q', 'a')")
    tenancy.backfill_org(spine)
    org = tenancy.default_org_id(spine)
    assert spine.read().execute("SELECT org_id FROM documents").fetchone()["org_id"] == org
    assert spine.read().execute("SELECT org_id FROM knowledge").fetchone()["org_id"] == org


def test_cache_isolated_per_org(spine):
    cache.put(spine, "koliki je pdv?", "25%", org_id=1)
    assert cache.get(spine, "koliki je pdv?", org_id=1) == "25%"
    assert cache.get(spine, "koliki je pdv?", org_id=2) is None
    assert cache.get(spine, "koliki je pdv?") is None  # global ključ ≠ org ključ


def test_kb_isolated_per_org(spine):
    kb.save(spine, "kako se salje joppd", "kroz eporeznu", org_id=1)
    assert kb.lookup(spine, "kako se salje joppd", org_id=1) == "kroz eporeznu"
    assert kb.lookup(spine, "kako se salje joppd", org_id=2) is None
