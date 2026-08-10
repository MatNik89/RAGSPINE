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


def test_handle_org_scope_hides_other_org(spine):
    # kg_edges su globalni: dokument DRUGE org ne smije iscuriti kroz graf (Codex HIGH)
    with spine.write() as c:
        c.execute("INSERT INTO documents(id, title, org_id) VALUES(10,'Moj',1)")
        c.execute("INSERT INTO documents(id, title, org_id) VALUES(11,'Tuđi',2)")
    g.index_doc(spine, 10, "OIB 69435151530 konto 4010")
    g.index_doc(spine, 11, "OIB 69435151530 konto 4010")
    out = g.handle(spine, None, "veza OIB 69435151530", llm=None, org_id=1)
    assert "10" in out and "11" not in out  # samo moja org


def test_index_doc_caps_entities(spine):
    # golem tekst s mnogo entiteta ne smije eksplodirati u milijune bridova
    text = " ".join(f"konto {4000+i}" for i in range(500))  # 500 konto entiteta
    g.index_doc(spine, 20, text)
    edges = spine.read().execute("SELECT COUNT(*) AS n FROM kg_edges WHERE doc_id=20").fetchone()["n"]
    assert edges <= g._MAX_ENTS * g._MAX_ENTS  # omeđeno capom, ne 500^2
