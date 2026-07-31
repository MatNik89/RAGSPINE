from ragspine.rag.citations import verify
from ragspine.rag.retrieval import Hit

H = [Hit(1, 1, "pdv", "Stopa PDV-a je 25%.", 1.0, "zakon"),
     Hit(2, 2, "min", "Minimalac je 970 EUR.", 0.9, "zakon")]


def test_valid_citations():
    r = verify("Stopa PDV-a je 25% [1], a minimalac 970 EUR [2].", H)
    assert r.ok and set(r.cited) == {1, 2} and r.confidence > 0.5


def test_no_citations_not_ok():
    r = verify("Stopa je 25%.", H)
    assert not r.ok


def test_out_of_range_ignored():
    r = verify("Nešto [7].", H)
    assert not r.ok and r.cited == []


def test_no_hits_no_requirement():
    r = verify("Pozdrav!", [])
    assert r.ok
