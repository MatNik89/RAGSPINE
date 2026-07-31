from ragspine.rag import cache

def test_roundtrip_and_normalize(spine):
    cache.put(spine, "Koliki je PDV?", "25% [1]")
    assert cache.get(spine, "koliki je  pdv?") == "25% [1]"

def test_expired(spine):
    cache.put(spine, "q", "a")
    with spine.write() as c:
        c.execute("UPDATE query_cache SET at=datetime('now','-25 hours')")
    assert cache.get(spine, "q") is None
