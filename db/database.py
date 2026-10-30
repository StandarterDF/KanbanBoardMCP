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


def _table_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return [r["name"] for r in cur.fetchall()]


def _ensure_column(cur, conn, table, col, ddl):
    cols = _table_columns(cur, table)
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


DEFAULT_COLUMNS = [
    ("Backlog", 0, "#6c757d"),
    ("In Progress", 1, "#0d6efd"),
    ("Implemented", 2, "#198754"),
    ("Tested", 3, "#fd7e14"),
    ("Committed", 4, "#6f42c1"),
]


def create_default_columns(cur, project_id: int):
    """Create the standard workflow columns for a project if it has none."""
    cur.execute("SELECT COUNT(*) AS c FROM columns WHERE project_id = ?", (project_id,))
    if cur.fetchone()["c"]:
        return
    for name, order_index, color in DEFAULT_COLUMNS:
        cur.execute(
            "INSERT INTO columns (project_id, name, order_index, color) VALUES (?,?,?,?)",
            (project_id, name, order_index, color),
        )


def init_db():
    with db_cursor() as (cur, conn):
        cur.executescript(SCHEMA)
        migrate(cur, conn)
        seed_defaults(cur, conn)


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS columns (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    color TEXT NOT NULL DEFAULT '#4a90d9',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'Feature',
    priority TEXT NOT NULL DEFAULT 'Medium',
    column_id INTEGER NOT NULL,
    agent_id TEXT,
    affected_files TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (column_id) REFERENCES columns(id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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
    note TEXT,
    created_at TEXT NOT NULL
);
"""


def migrate(cur, conn):
    # ensure default project exists
    cur.execute("SELECT COUNT(*) AS c FROM projects")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO projects (name, slug, description, created_at) VALUES (?,?,?,?)",
            ("Default", "default", "", _now()),
        )
    # get default project id
    cur.execute("SELECT id FROM projects WHERE slug='default'")
    r = cur.fetchone()
    default_pid = r["id"] if r else 1

    # columns migration
    _ensure_column(cur, conn, "columns", "project_id", "INTEGER NOT NULL DEFAULT 1")
    cur.execute(
        "UPDATE columns SET project_id = ? WHERE project_id IS NULL", (default_pid,)
    )

    # tasks migration
    _ensure_column(cur, conn, "tasks", "project_id", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(cur, conn, "tasks", "affected_files", "TEXT DEFAULT ''")
    # backfill project_id from column's project
    cur.execute(
        """UPDATE tasks SET project_id = (
            SELECT c.project_id FROM columns c WHERE c.id = tasks.column_id
        ) WHERE project_id IS NULL OR project_id = 0"""
    )
    cur.execute(
        "UPDATE tasks SET project_id = ? WHERE project_id IS NULL", (default_pid,)
    )
    cur.execute("UPDATE tasks SET affected_files = '' WHERE affected_files IS NULL")

    # audit_log migration: optional human-readable note / comment
    _ensure_column(cur, conn, "audit_log", "note", "TEXT")

    # comments FK cascade already; nothing

    conn.commit()


def seed_defaults(cur, conn):
    # default project
    cur.execute("SELECT COUNT(*) AS c FROM projects")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO projects (name, slug, description, created_at) VALUES (?,?,?,?)",
            ("Default", "default", "", _now()),
        )
    cur.execute("SELECT id FROM projects WHERE slug='default'")
    r = cur.fetchone()
    default_pid = r["id"] if r else 1

    # columns: every project must have at least the standard workflow stages
    cur.execute("SELECT id FROM projects ORDER BY id")
    for prow in cur.fetchall():
        create_default_columns(cur, prow["id"])

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
