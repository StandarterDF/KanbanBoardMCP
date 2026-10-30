"""Project-scoped boards.

A project owns one main board (``is_default=1``, name ``Главная``) plus any
number of additional boards. Columns and categories stay project-wide; a board
only filters which tasks are shown.

Names are unique inside a project, case-insensitively (enforced both here and
by ``UNIQUE (project_id, name)`` with ``COLLATE NOCASE``).
"""

import sqlite3
from typing import Any, Dict, List, Optional

from db.database import db_cursor, now, row_to_dict
from services import project_service


_BOARD_SELECT = """
    SELECT b.*,
           (SELECT COUNT(*) FROM tasks t WHERE t.board_id = b.id) AS task_count
    FROM boards b
"""


def _project_id(project_id: Optional[int] = None) -> int:
    if project_id is not None:
        return project_id
    p = project_service.get_default_project()
    return p["id"] if p else 1


def list_boards(project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    pid = _project_id(project_id)
    with db_cursor() as (cur, conn):
        cur.execute(
            _BOARD_SELECT
            + """ WHERE b.project_id = ?
                ORDER BY b.is_default DESC, b.order_index, b.id""",
            (pid,),
        )
        return [row_to_dict(r) for r in cur.fetchall()]


def get_board(board_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute(_BOARD_SELECT + " WHERE b.id = ?", (board_id,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def get_default_board(project_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute(
            _BOARD_SELECT
            + """ WHERE b.project_id = ? AND b.is_default = 1
                  ORDER BY b.order_index, b.id LIMIT 1""",
            (project_id,),
        )
        r = cur.fetchone()
        if r:
            return row_to_dict(r)
        cur.execute(
            _BOARD_SELECT
            + " WHERE b.project_id = ? ORDER BY b.is_default DESC, b.order_index, b.id LIMIT 1",
            (project_id,),
        )
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def find_board_by_name(project_id: int, name: str) -> Optional[Dict[str, Any]]:
    # SQLite's NOCASE collation is ASCII-only, so compare with Python casefold
    # to support Cyrillic board names ("Главная" == "главная").
    target = str(name).strip().casefold()
    for board in list_boards(project_id):
        if board["name"].strip().casefold() == target:
            return board
    return None


def resolve_board(
    project_id: int, board: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """Resolve ``board`` (id, numeric string, name or None) within a project.

    ``None`` selects the main board, then the first board as a fallback.
    Returns ``None`` when the reference does not point to a board of the
    project.
    """
    if board is None or str(board).strip() == "":
        return get_default_board(project_id)
    if isinstance(board, int):
        candidate = get_board(board)
        return (
            candidate if candidate and candidate["project_id"] == project_id else None
        )
    raw = str(board).strip()
    if raw.isdigit():
        candidate = get_board(int(raw))
        return (
            candidate if candidate and candidate["project_id"] == project_id else None
        )
    return find_board_by_name(project_id, raw)


def board_task_count(board_id: int) -> int:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE board_id = ?", (board_id,))
        return cur.fetchone()["c"]


def create_board(project_id: Optional[int], name: str) -> Dict[str, Any]:
    pid = _project_id(project_id)
    if project_service.get_project(pid) is None:
        raise ValueError(f"Project {pid} not found")
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Board name is required")
    if find_board_by_name(pid, clean):
        raise ValueError(f"Board '{clean}' already exists in this project")
    with db_cursor() as (cur, conn):
        cur.execute(
            "SELECT COALESCE(MAX(order_index), -1) AS m FROM boards WHERE project_id = ?",
            (pid,),
        )
        order_index = cur.fetchone()["m"] + 1
        try:
            cur.execute(
                """INSERT INTO boards (project_id, name, is_default, order_index, created_at)
                   VALUES (?,?,?,?,?)""",
                (pid, clean, 0, order_index, now()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Board '{clean}' already exists in this project")
        board_id = cur.lastrowid
    return get_board(board_id)


def update_board(
    board_id: int,
    name: Optional[str] = None,
    order_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    board = get_board(board_id)
    if not board:
        return None
    fields: Dict[str, Any] = {}
    if name is not None:
        clean = name.strip()
        if not clean:
            raise ValueError("Board name is required")
        other = find_board_by_name(board["project_id"], clean)
        if other and other["id"] != board_id:
            raise ValueError(f"Board '{clean}' already exists in this project")
        fields["name"] = clean
    if order_index is not None:
        fields["order_index"] = order_index
    if not fields:
        return board
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [board_id]
    with db_cursor() as (cur, conn):
        try:
            cur.execute(f"UPDATE boards SET {sets} WHERE id = ?", params)
        except sqlite3.IntegrityError:
            raise ValueError(
                f"Board '{fields.get('name', board['name'])}' already exists in this project"
            )
    return get_board(board_id)


def delete_board(board_id: int) -> bool:
    board = get_board(board_id)
    if not board:
        return False
    if board["is_default"]:
        raise ValueError("Cannot delete the main board of a project")
    count = board_task_count(board_id)
    if count:
        raise ValueError(
            f"Cannot delete board '{board['name']}': {count} task(s) use it"
        )
    with db_cursor() as (cur, conn):
        cur.execute("DELETE FROM boards WHERE id = ?", (board_id,))
        return cur.rowcount > 0
