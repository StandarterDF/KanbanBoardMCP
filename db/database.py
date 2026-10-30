"""SQLite database helpers with WAL mode."""

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tracker.db"
)

_lock = threading.RLock()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def db_cursor():
    with _lock:
        conn = get_connection()
        try:
            cur = conn.cursor()
            yield cur, conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    with db_cursor() as (cur, conn):
        cur.executescript(SCHEMA)
        seed_defaults(cur, conn)


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS columns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    color TEXT NOT NULL DEFAULT '#4a90d9'
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'Feature',
    priority TEXT NOT NULL DEFAULT 'Medium',
    column_id INTEGER NOT NULL,
    agent_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (column_id) REFERENCES columns(id)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    last_active TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS mcp_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    actor TEXT DEFAULT 'system',
    created_at TEXT NOT NULL
);
"""


def seed_defaults(cur, conn):
    # columns
    cur.execute("SELECT COUNT(*) AS c FROM columns")
    if cur.fetchone()["c"] == 0:
        defaults = [
            (1, "Backlog", 0, "#6c757d"),
            (2, "In Progress", 1, "#0d6efd"),
            (3, "Implemented", 2, "#198754"),
            (4, "Tested", 3, "#fd7e14"),
            (5, "Committed", 4, "#6f42c1"),
        ]
        cur.executemany(
            "INSERT INTO columns (id, name, order_index, color) VALUES (?,?,?,?)",
            defaults,
        )

    # mcp_settings
    settings = [
        ("allow_agent_create_tasks", "true", "Allow AI agent to create tasks", 1),
        ("allow_agent_comment", "true", "Allow AI agent to add comments", 1),
        (
            "enable_proactive_notifications",
            "true",
            "Allow system to notify about stalled tasks",
            1,
        ),
        (
            "strict_move_confirmation",
            "true",
            "Require explicit user confirmation for task moves",
            1,
        ),
    ]
    for key, value, desc, enabled in settings:
        cur.execute(
            "INSERT OR IGNORE INTO mcp_settings (key, value, description, is_enabled) VALUES (?,?,?,?)",
            (key, value, desc, enabled),
        )


def row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def now():
    return _now()
