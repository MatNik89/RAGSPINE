"""Restringirani radnik ne smije vidjeti agregate/imena skrivenih klijenata kroz
monthly/clarify/sql lane (Codex #2). visible=None → bez filtera (manager)."""
from ragspine.business import monthly
from ragspine.rag import clarify, sql_lane


def _clients(spine, *specs):
    ids = {}
    with spine.write() as c:
        for name, oib in specs:
            ids[name] = c.execute("INSERT INTO clients(name, oib) VALUES(?,?)", (name, oib)).lastrowid
    return ids


def test_sql_racuna_scoped_by_oib(spine):
    ids = _clients(spine, ("Alfa", "11111111119"), ("Beta", "22222222227"))
    with spine.write() as c:
        c.execute("INSERT INTO eracuni(customer_oib, vat, issued) VALUES('11111111119', 10, '2026-01-01')")
        c.execute("INSERT INTO eracuni(customer_oib, vat, issued) VALUES('22222222227', 20, '2026-01-01')")
    assert "2" in sql_lane.handle(spine, "koliko je računa")           # svi (manager)
    r = sql_lane.handle(spine, "koliko je računa", visible={ids["Alfa"]})
    assert "1" in r and "2" not in r                                    # samo Alfa
    pdv = sql_lane.handle(spine, "ukupno pdv", visible={ids["Alfa"]})
    assert "10" in pdv                                                  # samo Alfin PDV


def test_sql_klijenata_i_dokumenata_scoped(spine):
    ids = _clients(spine, ("Alfa", "1"), ("Beta", "2"), ("Cezar", "3"))
    with spine.write() as c:
        c.execute("INSERT INTO documents(title, client_id) VALUES('d1', ?)", (ids["Alfa"],))
        c.execute("INSERT INTO documents(title, client_id) VALUES('d2', ?)", (ids["Beta"],))
        c.execute("INSERT INTO documents(title, client_id) VALUES('office', NULL)")
    assert "3" in sql_lane.handle(spine, "koliko klijenata")
    assert "1" in sql_lane.handle(spine, "koliko klijenata", visible={ids["Alfa"]})
    # dokumenti: Alfin (1) + uredski NULL (1) = 2
    assert "2" in sql_lane.handle(spine, "koliko dokumenata", visible={ids["Alfa"]})


def test_sql_empty_visible_sees_nothing(spine):
    _clients(spine, ("Alfa", "1"))
    assert "0" in sql_lane.handle(spine, "koliko klijenata", visible=set())


def test_monthly_scopes_client_lists(spine):
    ids = _clients(spine, ("Alfa", "1"), ("Beta", "2"))
    from datetime import date
    soon = date.today().replace(day=15).isoformat()
    with spine.write() as c:
        c.execute("INSERT INTO expiry_items(client_id, kind, label, expires) VALUES(?,?,?,?)", (ids["Alfa"], "x", "A", soon))
        c.execute("INSERT INTO expiry_items(client_id, kind, label, expires) VALUES(?,?,?,?)", (ids["Beta"], "x", "B", soon))
        c.execute("INSERT INTO notes(client_id, author, body) VALUES(?,?,?)", (ids["Beta"], "ana", "tajno"))
    ov = monthly.overview(spine, monthly._period_now(), visible={ids["Alfa"]})
    assert all(e["client_id"] == ids["Alfa"] for e in ov["expiring"])
    assert all(n["client_id"] == ids["Alfa"] for n in ov["recent_notes"])
    # manager (None) vidi oba
    ov2 = monthly.overview(spine, monthly._period_now(), visible=None)
    assert len(ov2["expiring"]) == 2


def test_clarify_hides_hidden_client_variants(spine):
    ids = _clients(spine, ("Alfa", "1"), ("Beta", "2"))
    with spine.write() as c:
        for cid in (ids["Alfa"], ids["Beta"]):
            c.execute("INSERT INTO sop_pages(title, client_id, category, content, status) "
                      "VALUES('placa', ?, 'placa', 'kako', 'approved')", (cid,))
    # restringiran na Alfa: <2 vidljive varijante → nema pitanja koje otkriva Betu
    res = clarify.needs_clarification(spine, "kako se radi placa", visible={ids["Alfa"]})
    if res is not None:
        assert all(v.get("client_id") in (None, ids["Alfa"]) for v in res["variants"])


def test_answer_sql_scoped_for_restricted_actor(spine, cfg):
    from ragspine.rag import pipeline
    from ragspine.business.acl import Actor
    from ragspine.business import client_visibility as cv
    from ragspine.web.deps import add_user
    ids = _clients(spine, ("Alfa", "1"), ("Beta", "2"), ("Cezar", "3"))
    add_user(spine, "boris", "pw")
    uid = spine.read().execute("SELECT id FROM users WHERE username='boris'").fetchone()["id"]
    cv.set_policy(spine, uid, sees_all=False, client_ids=[ids["Alfa"]])
    restricted = Actor(user_id=uid, org_id=1, role="member", username="boris")
    res = pipeline.answer(spine, cfg, "koliko klijenata", "boris", actor=restricted)
    assert res["lane"] == "sql" and "1" in res["answer"]  # vidi samo Alfa
    # manager vidi sve 3
    owner = Actor(user_id=1, org_id=1, role="owner", username="ana")
    res2 = pipeline.answer(spine, cfg, "koliko klijenata", "ana", actor=owner)
    assert "3" in res2["answer"]


def test_monthly_unsent_obveze_scoped(spine):
    from ragspine.business import obveze
    ids = _clients(spine, ("Alfa", "1"), ("Beta", "2"))
    with spine.write() as c:
        for cid in (ids["Alfa"], ids["Beta"]):
            c.execute("INSERT INTO obligations(client_id, kind, period) VALUES(?,'PDV','2026-01')", (cid,))
    rows = obveze.list_period(spine, "PDV", "2026-01")
    assert all("client_id" in r.keys() for r in rows) and len(rows) == 2   # fix: client_id vraćen
    # monthly._vis filtrira po client_id
    vis = monthly._vis([dict(r) for r in rows], {ids["Alfa"]})
    assert vis and all(o["client_id"] == ids["Alfa"] for o in vis)
    assert len(monthly._vis([dict(r) for r in rows], None)) == 2


def test_graph_lane_scoped(spine):
    from ragspine.rag import graphrag
    ids = _clients(spine, ("Alfa", "1"), ("Beta", "2"))
    oib = "12345678903"
    with spine.write() as c:
        node = c.execute("INSERT INTO kg_nodes(kind, value) VALUES('oib', ?)", (oib,)).lastrowid
        da = c.execute("INSERT INTO documents(title, client_id) VALUES('Alfa dok', ?)", (ids["Alfa"],)).lastrowid
        db = c.execute("INSERT INTO documents(title, client_id) VALUES('Beta dok', ?)", (ids["Beta"],)).lastrowid
        c.execute("INSERT INTO kg_edges(src, dst, rel, doc_id) VALUES(?,?,'x',?)", (node, node, da))
        c.execute("INSERT INTO kg_edges(src, dst, rel, doc_id) VALUES(?,?,'x',?)", (node, node, db))
    q = f"što ima za OIB {oib}"
    def doc_ids(text):
        seg = text.split("Dokumenti:")[1]
        import re
        return set(re.findall(r"\d+", seg))
    full = graphrag.handle(spine, None, q, None, visible=None)
    assert doc_ids(full) == {str(da), str(db)}                    # manager oba
    scoped = graphrag.handle(spine, None, q, None, visible={ids["Alfa"]})
    assert doc_ids(scoped) == {str(da)}                            # samo Alfin
