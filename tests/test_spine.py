import threading
from ragspine.core.spine import Spine, _ensure_columns
from ragspine.ops import wizard_state as ws
from ragspine.web.firstrun import create_first_owner

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

def test_ensure_columns_adds_missing_column_with_default(tmp_path):
    sp = _sp(tmp_path)
    with sp.write() as c:
        c.execute("CREATE TABLE widgets(id INTEGER PRIMARY KEY, name TEXT)")
        c.execute("INSERT INTO widgets(id, name) VALUES(1, 'a')")
        _ensure_columns(c, "widgets", {"flag": "INTEGER DEFAULT 0"})
    cols = {r["name"] for r in sp.read().execute("PRAGMA table_info(widgets)").fetchall()}
    assert "flag" in cols
    row = sp.read().execute("SELECT flag FROM widgets WHERE id=1").fetchone()
    assert row["flag"] == 0  # existing rows backfilled with the column default


def test_ensure_columns_idempotent_noop_when_present(tmp_path):
    sp = _sp(tmp_path)
    with sp.write() as c:
        c.execute("CREATE TABLE widgets(id INTEGER PRIMARY KEY, name TEXT, flag INTEGER DEFAULT 0)")
        _ensure_columns(c, "widgets", {"flag": "INTEGER DEFAULT 0"})  # must not raise (duplicate column)
    cols = [r["name"] for r in sp.read().execute("PRAGMA table_info(widgets)").fetchall()]
    assert cols.count("flag") == 1


def test_clients_messaging_columns_present(tmp_path):
    # real deployment gap this closes: CREATE TABLE IF NOT EXISTS is a no-op
    # on a pre-existing DB, so the migration must actually add these columns.
    sp = _sp(tmp_path)
    cols = {r["name"] for r in sp.read().execute("PRAGMA table_info(clients)").fetchall()}
    assert {"messaging_consent", "messaging_channel", "messaging_target"} <= cols


def test_migration_adds_messaging_columns_to_preexisting_db(tmp_path):
    # simulates a real deployed DB created before the messaging columns existed:
    # clients table pre-exists WITHOUT them, so CREATE TABLE IF NOT EXISTS alone
    # would be a no-op — the migration must still add them on next Spine init.
    import sqlite3
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT, oib TEXT UNIQUE)")
    conn.execute("INSERT INTO clients(id, name, oib) VALUES(1, 'Stari', '1')")
    conn.commit(); conn.close()

    sp = Spine(db_path)

    cols = {r["name"] for r in sp.read().execute("PRAGMA table_info(clients)").fetchall()}
    assert {"messaging_consent", "messaging_channel", "messaging_target"} <= cols
    row = sp.read().execute("SELECT messaging_consent FROM clients WHERE id=1").fetchone()
    assert row["messaging_consent"] == 0


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

def test_migration_ne_gazi_resume_kad_stage_red_postoji(tmp_path):
    # wizard pokrenut, ali nedovršen (npr. pad usred wizarda nakon prvog
    # set_stage): sljedeći ragspine setup u NOVOM procesu (nova Spine/init_spine
    # nad istim db_path) ne smije auto-dovršiti setup — mora ostati resumable.
    db_path = str(tmp_path / "resume.db")
    s1 = Spine(db_path)
    create_first_owner(s1, "admin", "lozinka123")
    ws.set_stage(s1, 2)
    assert ws.is_complete(s1) is False

    s2 = Spine(db_path)  # nov proces = novi Spine.__init__ nad istom bazom
    assert ws.is_complete(s2) is False, "resume treba ostati moguć, ne auto-complete"
    assert ws.get_stage(s2) == 2


def test_migration_legacy_bazu_bez_stage_reda_i_dalje_auto_dovrsava(tmp_path):
    # legacy slučaj (baza radila prije wizarda): korisnik postoji, ALI nema
    # nikakvih module='setup' redova (create_first_owner ih ne dira) —
    # migracija na sljedećem otvaranju baze mora postaviti complete=true.
    db_path = str(tmp_path / "legacy.db")
    s1 = Spine(db_path)
    create_first_owner(s1, "admin", "lozinka123")
    rows = s1.read().execute(
        "SELECT * FROM config_overrides WHERE module='setup'").fetchall()
    assert rows == [], "create_first_owner ne smije dirati setup redove"

    s2 = Spine(db_path)  # reopen = migracija se ponovno izvodi
    assert ws.is_complete(s2) is True


def test_chunks_fts_update(tmp_path):
    sp = _sp(tmp_path)
    with sp.write() as c:
        c.execute("INSERT INTO chunks(doc_id, seq, text, title) VALUES (1, 1, ?, ?)", ("hello world", "greeting"))
    with sp.write() as c:
        c.execute("UPDATE chunks SET text=? WHERE id=1", ("goodbye",))
    rc = sp.read().execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'goodbye'").fetchone()[0]
    assert rc == 1, "UPDATE should sync chunks_fts"
    rc = sp.read().execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'hello'").fetchone()[0]
    assert rc == 0, "Old text should be removed from chunks_fts"
