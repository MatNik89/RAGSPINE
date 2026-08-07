from atlas.rag import graphrag as g

def test_extract():
    ents = dict(g.extract_entities("Klijent OIB 69435151530, konto 4010, iznos 1.500,00 EUR"))
    assert ents["oib"] == "69435151530" and ents["konto"] == "4010"

def test_index_and_traverse(spine):
    g.index_doc(spine, 1, "OIB 69435151530 konto 4010")
    g.index_doc(spine, 2, "OIB 69435151530 konto 7500")
    n = spine.read().execute("SELECT id FROM kg_nodes WHERE kind='oib'").fetchone()["id"]
    reach = g.traverse(spine, [n], hops=2)
    kinds = {spine.read().execute("SELECT kind FROM kg_nodes WHERE id=?", (i,)).fetchone()["kind"] for i in reach}
    assert "konto" in kinds
