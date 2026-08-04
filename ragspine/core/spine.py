import sqlite3, threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, title TEXT, path TEXT,
  doc_type TEXT, client_id INTEGER, sha256 TEXT UNIQUE, source_url TEXT,
  valid_from TEXT, valid_until TEXT, stale INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, seq INTEGER,
  text TEXT, title TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, title,
  content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text, title) VALUES (new.id, new.text, new.title); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, title) VALUES ('delete', old.id, old.text, old.title); END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, title) VALUES ('delete', old.id, old.text, old.title);
  INSERT INTO chunks_fts(rowid, text, title) VALUES (new.id, new.text, new.title); END;
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT, oib TEXT UNIQUE,
  email TEXT, phone TEXT, owner TEXT, industry TEXT, pdv_status TEXT, nas_folder TEXT,
  active INTEGER DEFAULT 1, messaging_consent INTEGER DEFAULT 0,
  messaging_channel TEXT DEFAULT '', messaging_target TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE,
  pw_hash TEXT, role TEXT DEFAULT 'radnik', created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS config_overrides(module TEXT, key TEXT, value TEXT,
  source_url TEXT, updated_at TEXT DEFAULT (datetime('now')), PRIMARY KEY(module, key));
CREATE TABLE IF NOT EXISTS watch_sources(id INTEGER PRIMARY KEY, url TEXT UNIQUE,
  category TEXT, client_id INTEGER, added_by TEXT, active INTEGER DEFAULT 1, kind TEXT DEFAULT 'page');
CREATE TABLE IF NOT EXISTS watch_state(source_id INTEGER PRIMARY KEY, last_hash TEXT,
  last_checked TEXT, last_content TEXT);
CREATE TABLE IF NOT EXISTS law_versions(id INTEGER PRIMARY KEY, source_id INTEGER,
  content TEXT, fetched_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS upcoming_changes(id INTEGER PRIMARY KEY, source_id INTEGER,
  description TEXT, effective_date TEXT, detected_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS obligations(id INTEGER PRIMARY KEY, client_id INTEGER,
  kind TEXT, period TEXT, UNIQUE(client_id, kind, period));
CREATE TABLE IF NOT EXISTS obligation_status(obligation_id INTEGER PRIMARY KEY,
  sent INTEGER DEFAULT 0, sent_by TEXT, sent_at TEXT);
CREATE TABLE IF NOT EXISTS obligation_types(kind TEXT PRIMARY KEY, label TEXT,
  rule TEXT, frequency TEXT DEFAULT 'monthly', applies_to TEXT DEFAULT 'all_active',
  active INTEGER DEFAULT 1, sort INTEGER DEFAULT 100, description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS client_obligation_types(client_id INTEGER, kind TEXT,
  PRIMARY KEY(client_id, kind));
CREATE TABLE IF NOT EXISTS wiki_pages(id INTEGER PRIMARY KEY, org_id INTEGER,
  type TEXT, title TEXT, slug TEXT, body TEXT, locked INTEGER DEFAULT 0,
  version INTEGER DEFAULT 1, owner_user_id INTEGER, visibility TEXT DEFAULT 'org',
  team_id INTEGER, updated_at TEXT DEFAULT (datetime('now')), UNIQUE(org_id, slug));
CREATE TABLE IF NOT EXISTS wiki_links(org_id INTEGER, src_page_id INTEGER, dst_slug TEXT,
  PRIMARY KEY(src_page_id, dst_slug));
CREATE TABLE IF NOT EXISTS wiki_sources(id INTEGER PRIMARY KEY, org_id INTEGER,
  source_key TEXT, sha256 TEXT, UNIQUE(org_id, source_key));
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(title, body, page_id UNINDEXED,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS orgs(id INTEGER PRIMARY KEY, name TEXT,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS mem_l0(id INTEGER PRIMARY KEY, org_id INTEGER, user_id INTEGER,
  session_id TEXT, role TEXT, content TEXT, distilled INTEGER DEFAULT 0,
  at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS mem_l1(id INTEGER PRIMARY KEY, org_id INTEGER, user_id INTEGER,
  kind TEXT, content TEXT, confidence REAL DEFAULT 0.8, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS mem_l3(org_id INTEGER, user_id INTEGER, persona TEXT,
  updated_at TEXT DEFAULT (datetime('now')), PRIMARY KEY(org_id, user_id));
CREATE TABLE IF NOT EXISTS skills(id INTEGER PRIMARY KEY, org_id INTEGER, name TEXT,
  description TEXT, trigger TEXT, steps TEXT, validation TEXT, version INTEGER DEFAULT 1,
  status TEXT DEFAULT 'draft', owner_user_id INTEGER, visibility TEXT DEFAULT 'private',
  team_id INTEGER, created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS memberships(id INTEGER PRIMARY KEY, org_id INTEGER,
  user_id INTEGER, role TEXT DEFAULT 'member', created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(org_id, user_id));
CREATE TABLE IF NOT EXISTS teams(id INTEGER PRIMARY KEY, org_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS team_members(team_id INTEGER, user_id INTEGER,
  PRIMARY KEY(team_id, user_id));
CREATE TABLE IF NOT EXISTS client_visibility(user_id INTEGER, client_id INTEGER,
  PRIMARY KEY(user_id, client_id));
CREATE TABLE IF NOT EXISTS folder_scan(folder_id INTEGER PRIMARY KEY,
  at TEXT DEFAULT (datetime('now')), n_subdirs INTEGER, n_docs INTEGER, n_pdf INTEGER,
  n_pdf_no_text INTEGER, summary_json TEXT);
CREATE TABLE IF NOT EXISTS asset_acl(id INTEGER PRIMARY KEY, asset_type TEXT,
  asset_id INTEGER, subject_type TEXT, subject_id TEXT, permission TEXT,
  UNIQUE(asset_type, asset_id, subject_type, subject_id, permission));
CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY, path TEXT UNIQUE,
  role TEXT, label TEXT, enabled INTEGER DEFAULT 1, added_by TEXT,
  added_at TEXT DEFAULT (datetime('now')), last_synced TEXT);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, client_id INTEGER, author TEXT,
  body TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS doc_types(key TEXT PRIMARY KEY, label TEXT,
  fields_json TEXT DEFAULT '[]', active INTEGER DEFAULT 1, sort INTEGER DEFAULT 100);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, user TEXT, action TEXT,
  entity TEXT, detail TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS hash_chain(id INTEGER PRIMARY KEY, event TEXT, prev_hash TEXT,
  hash TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge(id INTEGER PRIMARY KEY, question TEXT, answer TEXT,
  category TEXT, tags TEXT, hits INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, user TEXT, body TEXT,
  due TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, user TEXT, query TEXT,
  answer_id TEXT, score INTEGER, comment TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, user TEXT, key TEXT, value TEXT,
  UNIQUE(user, key));
CREATE TABLE IF NOT EXISTS kontni_plan(konto TEXT PRIMARY KEY, naziv TEXT, razred TEXT);
CREATE TABLE IF NOT EXISTS cjenik(id INTEGER PRIMARY KEY, usluga TEXT, cijena REAL, valuta TEXT DEFAULT 'EUR');
CREATE TABLE IF NOT EXISTS kg_nodes(id INTEGER PRIMARY KEY, kind TEXT, value TEXT, UNIQUE(kind, value));
CREATE TABLE IF NOT EXISTS kg_edges(src INTEGER, dst INTEGER, rel TEXT, doc_id INTEGER,
  PRIMARY KEY(src, dst, rel, doc_id));
CREATE TABLE IF NOT EXISTS interactions(id INTEGER PRIMARY KEY, user TEXT, query TEXT,
  lane TEXT, answer TEXT, confidence REAL, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS query_cache(qhash TEXT PRIMARY KEY, query TEXT, answer TEXT,
  meta TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS expiry_items(id INTEGER PRIMARY KEY, client_id INTEGER,
  kind TEXT, label TEXT, expires TEXT);
CREATE TABLE IF NOT EXISTS feature_requests(id INTEGER PRIMARY KEY, user TEXT, body TEXT,
  priority INTEGER DEFAULT 3, category TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS skill_suggestions(id INTEGER PRIMARY KEY, pattern TEXT,
  count INTEGER, params TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS imap_state(mailbox TEXT PRIMARY KEY, last_uid INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS browser_workflows(id INTEGER PRIMARY KEY, name TEXT UNIQUE, steps TEXT);
CREATE TABLE IF NOT EXISTS browser_sessions(host TEXT PRIMARY KEY, mode TEXT DEFAULT 'auto',
  failures INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS deadlines(id INTEGER PRIMARY KEY, kind TEXT, rule TEXT,
  description TEXT, UNIQUE(kind));
CREATE TABLE IF NOT EXISTS deadline_dates(id INTEGER PRIMARY KEY, kind TEXT, due TEXT, year INTEGER);
CREATE TABLE IF NOT EXISTS quickref(id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT,
  value TEXT, unit TEXT, category TEXT, source TEXT, keywords TEXT);
CREATE TABLE IF NOT EXISTS dnevnice_rates(country TEXT PRIMARY KEY, amount REAL,
  currency TEXT DEFAULT 'EUR', source TEXT);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, kind TEXT, body TEXT,
  client_id INTEGER, seen INTEGER DEFAULT 0, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS eracuni(id INTEGER PRIMARY KEY, doc_id INTEGER, supplier_oib TEXT,
  customer_oib TEXT, total REAL, vat REAL, currency TEXT, issued TEXT, raw_path TEXT);
CREATE TABLE IF NOT EXISTS konto_corrections(id INTEGER PRIMARY KEY, user TEXT, description TEXT,
  description_norm TEXT, original_konto TEXT, corrected_konto TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS message_log(id INTEGER PRIMARY KEY, client_id INTEGER, channel TEXT,
  status TEXT, subject TEXT, body_preview TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS peer_bookings(id INTEGER PRIMARY KEY, user TEXT, description TEXT,
  description_norm TEXT, konto TEXT, amount REAL, at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS sop_pages(id INTEGER PRIMARY KEY, title TEXT, client_id INTEGER,
  category TEXT, content TEXT, status TEXT DEFAULT 'draft', author TEXT, reviewer TEXT,
  base_version INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS sop_images(id INTEGER PRIMARY KEY, sop_id INTEGER, filename TEXT,
  path TEXT, ocr_text TEXT, caption TEXT, at TEXT DEFAULT (datetime('now')));
"""

def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Idempotent ALTER-TABLE migration: adds any column in `columns` (name ->
    'coltype [DEFAULT ...]') missing from `table`. CREATE TABLE IF NOT EXISTS
    is a no-op on a pre-existing DB, so new columns need this to actually
    reach deployed databases."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col, coldef in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")


class Spine:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._wlock = threading.Lock()
        with self.write() as c:
            c.executescript(SCHEMA)
            _ensure_columns(c, "clients", {
                "messaging_consent": "INTEGER DEFAULT 0",
                "messaging_channel": "TEXT DEFAULT ''",
                "messaging_target": "TEXT DEFAULT ''",
                "pausal_eur": "REAL DEFAULT 0",
                "has_employees": "INTEGER DEFAULT 0",
                "pdv_freq": "TEXT DEFAULT 'monthly'",
                "regime": "TEXT DEFAULT ''",
            })
            _ensure_columns(c, "documents", {
                "file_sha": "TEXT",
                "status": "TEXT DEFAULT 'active'",
                "supersedes": "INTEGER",
                "version": "INTEGER DEFAULT 1",
                "org_id": "INTEGER",
            })
            _ensure_columns(c, "knowledge", {"org_id": "INTEGER"})
            # sees_all_clients=1 (zadano) → radnik vidi sve klijente; 0 → samo
            # one iz client_visibility (per-radnik ograničenje vidljivosti)
            _ensure_columns(c, "users", {"sees_all_clients": "INTEGER DEFAULT 1"})
            _ensure_columns(c, "memory", {
                "hot_score": "REAL DEFAULT 1.0",
                "last_access": "TEXT DEFAULT (datetime('now'))",
                "access_count": "INTEGER DEFAULT 0",
            })
            _ensure_columns(c, "cjenik", {"key": "TEXT", "unit": "TEXT"})
            _ensure_columns(c, "folders", {"last_synced": "TEXT"})
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cjenik_key ON cjenik(key)")

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=10000")
            self._local.conn = c
        return c

    def read(self) -> sqlite3.Connection:
        return self._conn()

    @contextmanager
    def write(self):
        with self._wlock:
            c = self._conn()
            try:
                yield c; c.commit()
            except Exception:
                c.rollback(); raise

    def get_override(self, module, key, default=None):
        r = self.read().execute("SELECT value FROM config_overrides WHERE module=? AND key=?",
                                (module, key)).fetchone()
        return r["value"] if r else default

    def set_override(self, module, key, value, source_url=""):
        with self.write() as c:
            c.execute("""INSERT INTO config_overrides(module,key,value,source_url,updated_at)
                VALUES(?,?,?,?,datetime('now')) ON CONFLICT(module,key) DO UPDATE SET
                value=excluded.value, source_url=excluded.source_url, updated_at=excluded.updated_at""",
                (module, key, str(value), source_url))

    def audit(self, user, action, entity="", detail=""):
        with self.write() as c:
            c.execute("INSERT INTO audit_log(user,action,entity,detail) VALUES(?,?,?,?)",
                      (user, action, entity, detail))

    def close(self):
        c = getattr(self._local, "conn", None)
        if c: c.close(); self._local.conn = None

_spine: Spine | None = None
def init_spine(db_path: str) -> Spine:
    global _spine; _spine = Spine(db_path); return _spine
def get_spine() -> Spine:
    assert _spine is not None, "init_spine() nije pozvan"
    return _spine
