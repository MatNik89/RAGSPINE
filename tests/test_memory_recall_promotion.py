"""Recall-tracking (MateClaw steal): atomi koji se STVARNO dohvate dobiju
recall_count; na izjednačenom preklapanju rangiraju se više. Petlja mjeri
korišteno i promovira korisno."""
from atlas.knowledge import memory_layers as ml


def _atom(spine, content, org=1, uid=1, recall_count=0):
    with spine.write() as c:
        return c.execute(
            "INSERT INTO mem_l1(org_id,user_id,kind,content,recall_count) VALUES(?,?,?,?,?)",
            (org, uid, "fact", content, recall_count)).lastrowid


def _rc(spine, aid):
    return spine.read().execute(
        "SELECT recall_count FROM mem_l1 WHERE id=?", (aid,)).fetchone()["recall_count"]


def test_recall_increments_only_returned_atoms(spine):
    hit = _atom(spine, "klijent Pekara placa PDV mjesecno")
    miss = _atom(spine, "nepovezana biljeska o necemu drugom")
    out = ml.recall(spine, 1, 1, "kada Pekara placa PDV")
    assert any("Pekara" in a for a in out["atoms"])
    assert _rc(spine, hit) == 1      # dohvacen -> promoviran
    assert _rc(spine, miss) == 0     # nije dohvacen -> netaknut


def test_recall_count_breaks_ties(spine):
    # isto preklapanje s upitom, ali jedan je 'zasluzan' (visi recall_count)
    lo = _atom(spine, "Pekara racun", recall_count=0)
    hi = _atom(spine, "Pekara ponuda", recall_count=5)
    out = ml.recall(spine, 1, 1, "Pekara", max_items=1)
    assert out["atoms"] == ["Pekara ponuda"]  # zasluzni izbio gore
    assert _rc(spine, hi) == 6
    assert _rc(spine, lo) == 0


def test_recall_no_hits_no_write(spine):
    a = _atom(spine, "nista zajednicko")
    out = ml.recall(spine, 1, 1, "xyzzy plugh")
    assert out["atoms"] == []
    assert _rc(spine, a) == 0


def test_recall_org_scoped(spine):
    _atom(spine, "Pekara org1", org=1, uid=1)
    other = _atom(spine, "Pekara org2", org=2, uid=1)
    ml.recall(spine, 1, 1, "Pekara")
    assert _rc(spine, other) == 0  # druga org netaknuta
