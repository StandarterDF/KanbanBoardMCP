import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from db.database import db_cursor, now, row_to_dict
from models.schemas import (
    Task,
    TaskCreate,
    TaskUpdate,
    normalize_priority,
)
from services import project_service, history_service, category_service, boards_service


PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def _default_project_id(project_id: Optional[int] = None) -> int:
    if project_id is not None:
        return project_id
    p = project_service.get_default_project()
    return p["id"] if p else 1


def list_columns(project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    pid = _default_project_id(project_id)
    with db_cursor() as (cur, conn):
        cur.execute(
            "SELECT * FROM columns WHERE project_id = ? ORDER BY order_index, id",
            (pid,),
        )
        return [row_to_dict(r) for r in cur.fetchall()]


def get_column_by_name(
    name: str, project_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    pid = _default_project_id(project_id)
    with db_cursor() as (cur, conn):
        cur.execute(
            "SELECT * FROM columns WHERE name = ? AND project_id = ?", (name, pid)
        )
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def _stage_navigation(task: Dict[str, Any]) -> Dict[str, Any]:
    """Return ordered columns plus the position of the task's column.

    Adjacency is based on the actual sorted column list (``order_index, id``),
    never on numeric id differences, so gaps in ``order_index`` are harmless.
    """
    cols = list_columns(task["project_id"])
    ids = [c["id"] for c in cols]
    current_id = task["column_id"]
    index = ids.index(current_id) if current_id in ids else -1
    previous = cols[index - 1] if index > 0 else None
    following = cols[index + 1] if 0 <= index < len(cols) - 1 else None
    return {
        "columns": cols,
        "index": index,
        "current": cols[index] if index >= 0 else None,
        "previous": previous,
        "next": following,
    }


def ensure_adjacent_move(task: Dict[str, Any], target_column_id: int) -> None:
    """Raise ValueError unless ``target_column_id`` is the previous or next stage."""
    if task["column_id"] == target_column_id:
        return
    nav = _stage_navigation(task)
    if nav["index"] < 0:
        raise ValueError("Текущая колонка задачи не найдена в проекте")
    allowed_ids = {c["id"] for c in (nav["previous"], nav["next"]) if c is not None}
    if target_column_id in allowed_ids:
        return
    names = [c["name"] for c in (nav["previous"], nav["next"]) if c]
    if not names:
        raise ValueError("Нельзя перемещать задачу: в проекте нет соседних стадий.")
    if len(names) == 1:
        raise ValueError(f"Можно перемещать только на соседнюю стадию: {names[0]}.")
    raise ValueError(
        f"Можно перемещать только на соседнюю стадию: {names[0]} или {names[1]}."
    )


def get_column_by_id(
    cid: int, project_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        if project_id is not None:
            cur.execute(
                "SELECT * FROM columns WHERE id = ? AND project_id = ?",
                (cid, project_id),
            )
        else:
            cur.execute("SELECT * FROM columns WHERE id = ?", (cid,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def create_column(
    name: str,
    color: str = "#4a90d9",
    order_index: Optional[int] = None,
    project_id: Optional[int] = None,
) -> Dict[str, Any]:
    pid = _default_project_id(project_id)
    if order_index is None:
        cols = list_columns(pid)
        order_index = max((c["order_index"] for c in cols), default=-1) + 1
    with db_cursor() as (cur, conn):
        cur.execute(
            "INSERT INTO columns (project_id, name, order_index, color) VALUES (?,?,?,?)",
            (pid, name, order_index, color),
        )
        cid = cur.lastrowid
    return get_column_by_id(cid)


def update_column(
    col_id: int,
    name: Optional[str] = None,
    color: Optional[str] = None,
    order_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    col = get_column_by_id(col_id)
    if not col:
        return None
    fields = {}
    if name is not None:
        fields["name"] = name
    if color is not None:
        fields["color"] = color
    if order_index is not None:
        fields["order_index"] = order_index
    if not fields:
        return col
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [col_id]
    with db_cursor() as (cur, conn):
        cur.execute(f"UPDATE columns SET {sets} WHERE id = ?", params)
    return get_column_by_id(col_id)


def column_has_tasks(col_id: int) -> bool:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE column_id = ?", (col_id,))
        return cur.fetchone()["c"] > 0


def delete_column(col_id: int) -> bool:
    if column_has_tasks(col_id):
        raise ValueError("Column contains tasks")
    with db_cursor() as (cur, conn):
        cur.execute("DELETE FROM columns WHERE id = ?", (col_id,))
        return cur.rowcount > 0


def list_tasks(
    project_id: Optional[int] = None,
    column_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    priority: Optional[str] = None,
    priorities: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    q: Optional[str] = None,
    board_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    pid = _default_project_id(project_id)
    with db_cursor() as (cur, conn):
        sql = """
            SELECT t.*, c.name AS column_name, b.name AS board_name,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.task_id = t.id) AS comment_count
            FROM tasks t
            LEFT JOIN columns c ON c.id = t.column_id
            LEFT JOIN boards b ON b.id = t.board_id
            WHERE t.project_id = ?
        """
        params: list = [pid]
        if board_id is not None:
            sql += " AND t.board_id = ?"
            params.append(board_id)
        if column_id is not None:
            sql += " AND t.column_id = ?"
            params.append(column_id)
        if agent_id is not None:
            sql += " AND t.agent_id = ?"
            params.append(agent_id)
        if priority is not None:
            sql += " AND t.priority = ?"
            params.append(priority)
        if priorities:
            placeholders = ",".join("?" for _ in priorities)
            sql += f" AND t.priority IN ({placeholders})"
            params.extend(priorities)
        if categories:
            placeholders = ",".join("?" for _ in categories)
            sql += f" AND t.category IN ({placeholders})"
            params.extend(categories)
        if q:
            sql += " AND (t.title LIKE ? OR t.description LIKE ? OR t.affected_files LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like])
        sql += " ORDER BY t.id DESC"
        cur.execute(sql, params)
        return [row_to_dict(r) for r in cur.fetchall()]


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute(
            """
            SELECT t.*, c.name AS column_name, b.name AS board_name,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.task_id = t.id) AS comment_count
            FROM tasks t
            LEFT JOIN columns c ON c.id = t.column_id
            LEFT JOIN boards b ON b.id = t.board_id
            WHERE t.id = ?
        """,
            (task_id,),
        )
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def create_task(
    title: str,
    description: str = "",
    category: str = "Feature",
    priority: str = "Medium",
    column_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    affected_files: str = "",
    project_id: Optional[int] = None,
    board_id: Optional[int] = None,
) -> Dict[str, Any]:
    pid = _default_project_id(project_id)
    category = category_service.resolve_category(pid, category)
    priority = normalize_priority(priority)
    if board_id is None:
        board = boards_service.get_default_board(pid)
        if board is None:
            raise ValueError(f"Project {pid} has no boards")
        board_id = board["id"]
    else:
        board = boards_service.get_board(board_id)
        if board is None or board["project_id"] != pid:
            raise ValueError(f"Board {board_id} does not belong to project {pid}")
    if column_id is None:
        col = get_column_by_name("Backlog", pid)
        if col is None:
            cols = list_columns(pid)
            if not cols:
                raise ValueError("Project has no columns")
            col = cols[0]
        column_id = col["id"]
    elif get_column_by_id(column_id, project_id=pid) is None:
        raise ValueError(f"Column {column_id} does not belong to project {pid}")
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            """
            INSERT INTO tasks (project_id, board_id, title, description, category, priority, column_id, agent_id, affected_files, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                pid,
                board_id,
                title,
                description,
                category,
                priority,
                column_id,
                agent_id,
                affected_files or "",
                ts,
                ts,
            ),
        )
        tid = cur.lastrowid
        history_service.record_event(
            tid,
            "created",
            old_value=None,
            new_value=f"column_id={column_id}",
            actor=agent_id or "user",
            created_at=ts,
            cur=cur,
        )
    return get_task(tid)


def update_task(task_id: int, data, actor: str = "user") -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task:
        return None
    fields = {}
    if getattr(data, "title", None) is not None:
        fields["title"] = data.title
    if getattr(data, "description", None) is not None:
        fields["description"] = data.description
    if getattr(data, "category", None) is not None:
        fields["category"] = category_service.resolve_category(
            task["project_id"], data.category
        )
    if getattr(data, "priority", None) is not None:
        fields["priority"] = normalize_priority(data.priority)
    if getattr(data, "column_id", None) is not None:
        if get_column_by_id(data.column_id, project_id=task["project_id"]) is None:
            raise ValueError("Target column does not belong to the task project")
        if data.column_id != task["column_id"]:
            ensure_adjacent_move(task, data.column_id)
        fields["column_id"] = data.column_id
    if getattr(data, "agent_id", None) is not None:
        fields["agent_id"] = data.agent_id
    af = getattr(data, "affected_files", None)
    if af is not None:
        fields["affected_files"] = af
    board_id = getattr(data, "board_id", None)
    if board_id is not None:
        board = boards_service.get_board(board_id)
        if board is None or board["project_id"] != task["project_id"]:
            raise ValueError("Target board does not belong to the task project")
        fields["board_id"] = board_id
    if not fields:
        return task
    ts = now()
    changed = {k: v for k, v in fields.items() if task.get(k) != v}
    if not changed:
        return task
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [ts, task_id]
    with db_cursor() as (cur, conn):
        if "column_id" in changed:
            history_service.record_event(
                task_id,
                "moved",
                old_value=str(task["column_id"]),
                new_value=str(changed["column_id"]),
                actor=actor,
                created_at=ts,
                cur=cur,
            )
        field_changes = {k: v for k, v in changed.items() if k != "column_id"}
        if field_changes:
            history_service.record_event(
                task_id,
                "updated",
                old_value=history_service.dump_fields(
                    {k: task.get(k) for k in field_changes}
                ),
                new_value=history_service.dump_fields(field_changes),
                actor=actor,
                created_at=ts,
                cur=cur,
            )
        cur.execute(f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?", params)
    return get_task(task_id)


def move_task(
    task_id: int,
    target_column_id: int,
    actor: str = "user",
    note: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task:
        return None
    if get_column_by_id(target_column_id, project_id=task["project_id"]) is None:
        raise ValueError("Target column does not belong to the task project")
    if task["column_id"] == target_column_id:
        return task
    ensure_adjacent_move(task, target_column_id)
    ts = now()
    with db_cursor() as (cur, conn):
        history_service.record_event(
            task_id,
            "moved",
            old_value=str(task["column_id"]),
            new_value=str(target_column_id),
            actor=actor,
            note=note or None,
            created_at=ts,
            cur=cur,
        )
        cur.execute(
            "UPDATE tasks SET column_id = ?, updated_at = ? WHERE id = ?",
            (target_column_id, ts, task_id),
        )
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    with db_cursor() as (cur, conn):
        cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


def _parse_dt(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def stalled_tasks(
    hours: int = 24, project_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    pid = _default_project_id(project_id)
    with db_cursor() as (cur, conn):
        cur.execute(
            """
            SELECT t.*, c.name AS column_name,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.task_id = t.id) AS comment_count
            FROM tasks t LEFT JOIN columns c ON c.id = t.column_id
            WHERE t.project_id = ? AND t.priority IN ('High','Critical')
              AND c.name IN ('Backlog','In Progress')
            ORDER BY t.updated_at ASC
        """,
            (pid,),
        )
        rows = [row_to_dict(r) for r in cur.fetchall()]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for r in rows:
        if _parse_dt(r["updated_at"]) <= cutoff:
            out.append(r)
    return out


def analyze_next(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates = [
        t for t in tasks if t.get("column_name") in ("Backlog", "In Progress")
    ]
    if not candidates:
        candidates = tasks
    candidates.sort(
        key=lambda t: (PRIORITY_ORDER.get(t["priority"], 9), t.get("updated_at", ""))
    )
    if not candidates:
        return {"recommendation": None, "reason": "No tasks available."}
    t = candidates[0]
    return {
        "recommendation": t,
        "reason": f"Priority {t['priority']}, status {t.get('column_name')}.",
    }
