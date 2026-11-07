import sqlite3
from pathlib import Path
from contextlib import closing
SCHEMA = "CREATE TABLE IF NOT EXISTS products (\n id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,\n reorder_point INTEGER NOT NULL CHECK(reorder_point >= 0), version INTEGER NOT NULL DEFAULT 1);\nCREATE TABLE IF NOT EXISTS movements (\n id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),\n delta INTEGER NOT NULL CHECK(delta != 0), note TEXT NOT NULL,\n created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));\nCREATE TABLE IF NOT EXISTS movement_requests (\n request_key TEXT PRIMARY KEY, movement_id INTEGER NOT NULL REFERENCES movements(id),\n product_id INTEGER NOT NULL REFERENCES products(id), delta INTEGER NOT NULL,\n note TEXT NOT NULL, stock_after INTEGER NOT NULL);\nCREATE INDEX IF NOT EXISTS movements_product ON movements(product_id);"

def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)

def rows(cursor):
    return [dict(row) for row in cursor.fetchall()]
