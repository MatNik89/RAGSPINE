from atlas.rag import sql_lane

def _seed(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, oib) VALUES ('Firma A', '11111111119')")
        for i in range(3):
            c.execute("INSERT INTO eracuni(supplier_oib, total, vat, issued) VALUES (?, 100, 25, '2026-07-05')",
                      ("11111111119",))

def test_count_invoices(spine):
    _seed(spine)
    ans = sql_lane.handle(spine, "koliko računa imamo?")
    assert "3" in ans

def test_sum_vat(spine):
    _seed(spine)
    assert "75" in sql_lane.handle(spine, "zbroj pdv-a")

def test_no_template(spine):
    assert sql_lane.handle(spine, "koliko je sati?") is None
