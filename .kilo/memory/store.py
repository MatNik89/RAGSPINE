"""SQLite WAL spine for Kilo memory — the one database every module writes to.

Pattern: each thread gets its own connection, WAL serializes writers across threads+processes.
ponytail: one file, zero daemons, zero config. Default ~/.kilo/kilo_memory.db (KILO_MEMORY_PATH override).
"""

import atexit
import os
import sqlite3
import threading
from datetime import datetime, timezone

MEMORY_PATH = os.environ.get("KILO_MEMORY_PATH",
                              os.path.expanduser("~/.kilo/kilo_memory.db"))
os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)

write_lock = threading.RLock()
_local = threading.local()

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS mem_entries("
    "id INTEGER PRIMARY KEY, "
    "store TEXT, scope TEXT, content TEXT, source TEXT, "
    "confidence REAL DEFAULT 1.0, date TEXT, expiration TEXT DEFAULT 'never', "
    "hot INTEGER DEFAULT 1, tier TEXT DEFAULT 'model')",
    "CREATE INDEX IF NOT EXISTS idx_mem_entries_store ON mem_entries(store, hot)",
    "CREATE INDEX IF NOT EXISTS idx_mem_entries_scope ON mem_entries(store, scope, hot)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(content, scope, tokenize='unicode61')",
    "CREATE TABLE IF NOT EXISTS mem_vectors("
    "scope TEXT, hash TEXT, dim INT, vec BLOB, PRIMARY KEY(scope, hash))",
    "CREATE TABLE IF NOT EXISTS mem_assoc("
    "a TEXT, b TEXT, weight REAL DEFAULT 1, PRIMARY KEY(a, b))",
    "CREATE TABLE IF NOT EXISTS mem_stats("
    "hash TEXT PRIMARY KEY, scope TEXT, accesses INT DEFAULT 1, last_access TEXT)",
    "CREATE TABLE IF NOT EXISTS mem_migrated("
    "realpath TEXT PRIMARY KEY, ts REAL)",
]


def _ensure_schema(conn):
    for ddl in SCHEMA:
        conn.execute(ddl)
    conn.commit()


def get_conn():
    c = getattr(_local, "conn", None)
    if c is None:
        db = sqlite3.connect(MEMORY_PATH)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(db)
        _local.conn = db
    return _local.conn


def store():
    return get_conn()


def close():
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


atexit.register(close)
