import os

import pytest

from ragspine.docs import ingest as ing
from ragspine.rag import embed, retrieval


def _seed(spine):
    ing.ingest_text(spine, "Stopa PDV-a u Hrvatskoj je 25 posto, snižena 13 i 5.", "pdv-stope", doc_type="zakon")
    ing.ingest_text(spine, "Ugovor o najmu poslovnog prostora u Splitu.", "najam", doc_type="ugovor")
    ing.ingest_text(spine, "Minimalna plaća za 2026. iznosi X eura bruto.", "minimalac", doc_type="zakon")


def test_fts_search(spine):
    _seed(spine)
    hits = retrieval.search(spine, "kolika je stopa PDV-a?")
    assert hits and hits[0].title == "pdv-stope"


def test_rrf_fusion():
    fused = retrieval.rrf([[1, 2, 3], [2, 1, 4]])
    assert fused[2] > fused[3] and fused[1] > fused[4]


def test_freshness_excludes_stale(spine):
    _seed(spine)
    with spine.write() as c:
        c.execute("UPDATE documents SET stale=1 WHERE title='pdv-stope'")
    hits = retrieval.search(spine, "stopa PDV")
    assert all(h.title != "pdv-stope" for h in hits)


def test_hostile_fts_input(spine):
    _seed(spine)
    retrieval.search(spine, 'pdv" OR x(')  # ne smije raisati


def test_empty_query_returns_empty(spine):
    _seed(spine)
    assert retrieval.search(spine, "   ") == []


@pytest.mark.skipif(not embed.available(), reason="fastembed/sqlite_vec nisu instalirani")
@pytest.mark.skipif(os.environ.get("RAGSPINE_TEST_EMBED") != "1",
                     reason="postavi RAGSPINE_TEST_EMBED=1 za pravo učitavanje modela (mrežni download)")
def test_vec_path_with_real_model(spine):
    _seed(spine)
    hits = retrieval.search(spine, "PDV stopa")
    assert hits and hits[0].title == "pdv-stope"
