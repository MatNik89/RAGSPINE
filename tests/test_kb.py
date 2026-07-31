from ragspine.knowledge import kb

def test_similar_lookup(spine):
    kb.save(spine, "Koliki je prag za ulazak u sustav PDV-a?", "60.000 EUR", "pdv")
    assert kb.lookup(spine, "koji je prag za ulaz u pdv sustav?") == "60.000 EUR"
    assert kb.lookup(spine, "kako se knjiži amortizacija?") is None
