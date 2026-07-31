import threading
from ragspine.core.spine import Spine

def _sp(tmp_path): return Spine(str(tmp_path / "t.db"))

def test_schema_created(tmp_path):
    sp = _sp(tmp_path)
    names = {r[0] for r in sp.read().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ["documents", "chunks", "clients", "users", "watch_sources",
              "config_overrides", "audit_log", "kg_edges", "obligations",
              "query_cache", "knowledge", "kontni_plan", "hash_chain"]:
        assert t in names, t
    assert sp.read().execute("PRAGMA journal_mode").fetchone()[0] == "wal"

def test_override_roundtrip(tmp_path):
    sp = _sp(tmp_path)
    assert sp.get_override("kalkulator", "prirez.Split", "0") == "0"
    sp.set_override("kalkulator", "prirez.Split", "15.0", "https://porezna.hr")
    assert sp.get_override("kalkulator", "prirez.Split") == "15.0"

def test_concurrent_writes(tmp_path):
    sp = _sp(tmp_path)
    def w(i):
        with sp.write() as c:
            c.execute("INSERT INTO notes(client_id, author, body) VALUES (0,?,?)", ("t", str(i)))
    ts = [threading.Thread(target=w, args=(i,)) for i in range(20)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sp.read().execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 20

def test_audit(tmp_path):
    sp = _sp(tmp_path)
    sp.audit("ana", "pdv_sent", "client:3", "srpanj")
    r = sp.read().execute("SELECT user, action FROM audit_log").fetchone()
    assert (r["user"], r["action"]) == ("ana", "pdv_sent")
