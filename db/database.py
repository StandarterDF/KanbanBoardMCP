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

DEFAULT_CATEGORIES = [
    ("Feature", 0, "#5b8cff"),
    ("Bug", 1, "#ff5c5c"),
    ("Rework", 2, "#ffb47d"),
    ("Balance", 3, "#c9a6ff"),
    ("Cleanup", 4, "#3ddc97"),
]

DEFAULT_BOARD_NAME = "Главная"

_CATEGORY_PALETTE = [
    "#5b8cff",
    "#ff5c5c",
    "#ffb47d",
    "#c9a6ff",
    "#3ddc97",
    "#ffd166",
    "#4cc9f0",
    "#f78fb3",
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


def create_default_board(cur, project_id: int) -> int:
    """Return the id of the project's main board, creating it when missing.

    Idempotent: repeated calls never create duplicates. If a board named
    ``Главная`` already exists it is promoted to the main board instead of
    adding a second one.
    """
    cur.execute(
        "SELECT id FROM boards WHERE project_id = ? AND is_default = 1 ORDER BY id LIMIT 1",
        (project_id,),
    )
    row = cur.fetchone()
    if row:
        board_id = row["id"]
    else:
        # Python casefold because SQLite's NOCASE only folds ASCII.
        cur.execute(
            "SELECT id, name FROM boards WHERE project_id = ? ORDER BY id",
            (project_id,),
        )
        match = next(
            (
                r
                for r in cur.fetchall()
                if str(r["name"]).strip().casefold() == DEFAULT_BOARD_NAME.casefold()
            ),
            None,
        )
        if match:
            board_id = match["id"]
            cur.execute("UPDATE boards SET is_default = 1 WHERE id = ?", (board_id,))
        else:
            cur.execute(
                """INSERT INTO boards (project_id, name, is_default, order_index, created_at)
                   VALUES (?,?,?,?,?)""",
                (project_id, DEFAULT_BOARD_NAME, 1, 0, _now()),
            )
            board_id = cur.lastrowid
    cur.execute(
        "UPDATE boards SET is_default = 0 WHERE project_id = ? AND id <> ? AND is_default = 1",
        (project_id, board_id),
    )
    return board_id


def create_default_categories(cur, project_id: int):
    """Create the standard categories for a project if it has none."""
    cur.execute(
        "SELECT COUNT(*) AS c FROM categories WHERE project_id = ?", (project_id,)
    )
    if cur.fetchone()["c"]:
        return
    ts = _now()
    for name, order_index, color in DEFAULT_CATEGORIES:
        cur.execute(
            """INSERT OR IGNORE INTO categories
               (project_id, name, color, order_index, created_at)
               VALUES (?,?,?,?,?)""",
            (project_id, name, color, order_index, ts),
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

CREATE TABLE IF NOT EXISTS boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    is_default INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS columns (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    color TEXT NOT NULL DEFAULT '#4a90d9',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT '#5b8cff',
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL DEFAULT 1,
    board_id INTEGER,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'Feature',
    priority TEXT NOT NULL DEFAULT 'Medium',
    column_id INTEGER NOT NULL,
    agent_id TEXT,
    affected_files TEXT DEFAULT '',
    rejections INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    verified_by TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    acceptance_criteria TEXT DEFAULT '[]',
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
    kind TEXT NOT NULL DEFAULT 'message',
    images TEXT NOT NULL DEFAULT '[]',
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
    _ensure_column(cur, conn, "tasks", "rejections", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, conn, "tasks", "verified_at", "TEXT")
    _ensure_column(cur, conn, "tasks", "verified_by", "TEXT")
    _ensure_column(cur, conn, "tasks", "archived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cur, conn, "tasks", "archived_at", "TEXT")
    _ensure_column(cur, conn, "tasks", "acceptance_criteria", "TEXT DEFAULT '[]'")
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
    cur.execute("UPDATE tasks SET archived = 0 WHERE archived IS NULL")
    cur.execute(
        "UPDATE tasks SET acceptance_criteria = '[]' WHERE acceptance_criteria IS NULL"
    )

    # audit_log migration: optional human-readable note / comment
    _ensure_column(cur, conn, "audit_log", "note", "TEXT")

    # comments migration: distinguish plain messages from owner review decisions
    _ensure_column(cur, conn, "comments", "kind", "TEXT NOT NULL DEFAULT 'message'")
    _ensure_column(cur, conn, "comments", "images", "TEXT NOT NULL DEFAULT '[]'")
    cur.execute("UPDATE comments SET images = '[]' WHERE images IS NULL OR images = ''")
    cur.execute(
        "UPDATE comments SET kind = 'reject' "
        "WHERE kind = 'message' AND text LIKE 'Отклонено:%'"
    )
    cur.execute(
        "UPDATE comments SET kind = 'verify' "
        "WHERE kind = 'message' AND (text = 'Проверено.' OR text LIKE 'Проверено:%')"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_task_kind ON comments(task_id, kind, id)"
    )

    # boards migration: every project gets a main board, every task a board_id
    migrate_boards(cur, conn)

    # categories migration: defaults for every project + canonical task values
    migrate_categories(cur)

    conn.commit()


def migrate_boards(cur, conn):
    """Create the ``boards`` table data and attach tasks to a board.

    Idempotent. Existing tasks keep working: they are attached to the main
    board of their own project. Tasks pointing at a board of another project
    (or a missing board) are moved back to their project's main board.
    """
    _ensure_column(cur, conn, "tasks", "board_id", "INTEGER")
    cur.execute("SELECT id FROM projects ORDER BY id")
    project_ids = [r["id"] for r in cur.fetchall()]
    for pid in project_ids:
        default_board_id = create_default_board(cur, pid)
        cur.execute(
            """UPDATE tasks SET board_id = ?
               WHERE project_id = ?
                 AND (board_id IS NULL
                      OR board_id NOT IN (SELECT id FROM boards WHERE project_id = ?))""",
            (default_board_id, pid, pid),
        )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_board ON tasks(board_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_boards_project ON boards(project_id, order_index)"
    )


def _category_color(index: int) -> str:
    return _CATEGORY_PALETTE[index % len(_CATEGORY_PALETTE)]


def migrate_categories(cur):
    """Ensure every project has categories and every task points to one of them.

    Known default names are normalized case-insensitively (``balance`` ->
    ``Balance``). Unknown task categories are materialized as project categories
    so existing tasks never reference a missing category.
    """
    cur.execute("SELECT id FROM projects ORDER BY id")
    for prow in cur.fetchall():
        pid = prow["id"]
        create_default_categories(cur, pid)
        cur.execute(
            "SELECT name FROM categories WHERE project_id = ? ORDER BY order_index, id",
            (pid,),
        )
        ordered = [r["name"] for r in cur.fetchall()]
        by_key = {name.strip().casefold(): name for name in ordered}
        fallback = ordered[0] if ordered else None

        cur.execute("SELECT DISTINCT category FROM tasks WHERE project_id = ?", (pid,))
        raw_values = [r["category"] for r in cur.fetchall()]
        for raw in raw_values:
            if raw is None or str(raw).strip() == "":
                if fallback:
                    cur.execute(
                        """UPDATE tasks SET category = ?
                           WHERE project_id = ?
                             AND (category IS NULL OR TRIM(category) = '')""",
                        (fallback, pid),
                    )
                continue
            key = str(raw).strip().casefold()
            canonical = by_key.get(key)
            if canonical is None:
                canonical = str(raw).strip()
                color = _category_color(len(ordered))
                cur.execute(
                    """INSERT OR IGNORE INTO categories
                       (project_id, name, color, order_index, created_at)
                       VALUES (?,?,?,?,?)""",
                    (pid, canonical, color, len(ordered), _now()),
                )
                by_key[key] = canonical
                ordered.append(canonical)
            cur.execute(
                """UPDATE tasks SET category = ?
                   WHERE project_id = ? AND category = ? COLLATE NOCASE
                     AND category <> ?""",
                (canonical, pid, raw, canonical),
            )


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
        create_default_categories(cur, prow["id"])
        create_default_board(cur, prow["id"])

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
