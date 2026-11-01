import sqlite3
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from db.database import db_cursor, now, row_to_dict
from models.schemas import (
    Task,
    TaskCreate,
    TaskUpdate,
    normalize_priority,
)
from services import (
    project_service,
    history_service,
    category_service,
    boards_service,
    comment_service,
)


PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

# Comment summary is computed in SQL so reading a task list stays one query
# instead of one extra query per task (N+1).
_COMMENT_SUMMARY_SQL = """
    (SELECT COUNT(*) FROM comments cm WHERE cm.task_id = t.id) AS comment_count,
    (SELECT MAX(cm.id) FROM comments cm WHERE cm.task_id = t.id) AS last_comment_id,
    (SELECT MAX(cm.created_at) FROM comments cm WHERE cm.task_id = t.id) AS last_comment_at,
    (SELECT cm.author FROM comments cm WHERE cm.task_id = t.id
        ORDER BY cm.id DESC LIMIT 1) AS last_comment_author,
    (SELECT substr(cm.text, 1, 280) FROM comments cm WHERE cm.task_id = t.id
        ORDER BY cm.id DESC LIMIT 1) AS last_comment_text,
    (SELECT LENGTH(cm.text) FROM comments cm WHERE cm.task_id = t.id
        ORDER BY cm.id DESC LIMIT 1) AS last_comment_length,
    (SELECT cm.kind FROM comments cm WHERE cm.task_id = t.id
        ORDER BY cm.id DESC LIMIT 1) AS last_comment_kind
"""


def _parse_criteria(raw) -> List[Dict[str, Any]]:
    """Normalize the stored acceptance criteria JSON into a list of dicts."""
    if raw is None:
        return []
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw) if raw.strip() else []
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, str):
            out.append(
                {
                    "text": item.strip(),
                    "done": False,
                    "evidence": "",
                    "checked_at": None,
                    "checked_by": None,
                }
            )
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "text": text,
                    "done": bool(item.get("done")),
                    "evidence": str(item.get("evidence") or ""),
                    "checked_at": item.get("checked_at"),
                    "checked_by": item.get("checked_by"),
                }
            )
    return out


def _dump_criteria(criteria: List[Dict[str, Any]]) -> str:
    return json.dumps(criteria, ensure_ascii=False, default=str)


def attach_criteria(task: Dict[str, Any]) -> Dict[str, Any]:
    criteria = _parse_criteria(task.get("acceptance_criteria"))
    task["acceptance"] = criteria
    task["criteria_total"] = len(criteria)
    task["criteria_done"] = sum(1 for c in criteria if c.get("done"))
    return task


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


def get_stage_navigation(task: Dict[str, Any]) -> Dict[str, Any]:
    """Public wrapper around stage navigation (used by MCP for allowed moves)."""
    return _stage_navigation(task)


def active_stage_ids(project_id: Optional[int] = None) -> List[int]:
    """Return ids of the active stages: the first two columns by position.

    Stages are positional (``order_index, id``), never name-based, so projects
    with localized column names (``Бэклог``/``В работе``) work the same way as
    the English defaults.
    """
    cols = list_columns(project_id)
    return [c["id"] for c in cols[:2]]


def active_work_column(project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Return the ``In Progress``-style stage: the second column by position."""
    cols = list_columns(project_id)
    if not cols:
        return None
    return cols[1] if len(cols) > 1 else cols[0]


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
    updated_since: Optional[str] = None,
    order_by: str = "id",
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    pid = _default_project_id(project_id)
    with db_cursor() as (cur, conn):
        sql = f"""
            SELECT t.*, c.name AS column_name, b.name AS board_name,
                   {_COMMENT_SUMMARY_SQL}
            FROM tasks t
            LEFT JOIN columns c ON c.id = t.column_id
            LEFT JOIN boards b ON b.id = t.board_id
            WHERE t.project_id = ?
        """
        params: list = [pid]
        if not include_archived:
            sql += " AND COALESCE(t.archived, 0) = 0"
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
        if updated_since:
            sql += " AND t.updated_at >= ?"
            params.append(updated_since)
        if order_by == "activity":
            sql += " ORDER BY COALESCE(t.updated_at, '') DESC, t.id DESC"
        else:
            sql += " ORDER BY t.id DESC"
        cur.execute(sql, params)
        return [attach_criteria(row_to_dict(r)) for r in cur.fetchall()]


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute(
            f"""
            SELECT t.*, c.name AS column_name, b.name AS board_name,
                   {_COMMENT_SUMMARY_SQL}
            FROM tasks t
            LEFT JOIN columns c ON c.id = t.column_id
            LEFT JOIN boards b ON b.id = t.board_id
            WHERE t.id = ?
        """,
            (task_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        task = attach_criteria(row_to_dict(r))
    task["review_comment"] = comment_service.get_last_review_comment(task_id)
    return task


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
    require_board: bool = False,
    acceptance_criteria=None,
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
    if (
        require_board
        and board.get("is_default")
        and boards_service.has_additional_boards(pid)
    ):
        names = ", ".join(
            b["name"]
            for b in boards_service.list_boards(pid)
            if not b.get("is_default")
        )
        raise ValueError(
            "Choose a task board: the main board shows all project tasks and "
            f"cannot be a creation target. Available boards: {names}."
        )
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
    criteria_json = _dump_criteria(_parse_criteria(acceptance_criteria))
    with db_cursor() as (cur, conn):
        cur.execute(
            """
            INSERT INTO tasks (project_id, board_id, title, description, category, priority, column_id, agent_id, affected_files, acceptance_criteria, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
                criteria_json,
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
    hours: int = 24,
    project_id: Optional[int] = None,
    board_id: Optional[int] = None,
    priorities: Optional[List[str]] = None,
    stage_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Return tasks that had no activity for ``hours`` in the active stages.

    ``stage_ids`` overrides the default active stages (first two columns).
    ``priorities`` limits the result; ``None`` means every priority.
    """
    pid = _default_project_id(project_id)
    if stage_ids is None:
        stage_ids = active_stage_ids(pid)
    if not stage_ids:
        return []
    placeholders = ",".join("?" for _ in stage_ids)
    sql = f"""
        SELECT t.*, c.name AS column_name, b.name AS board_name,
               {_COMMENT_SUMMARY_SQL}
        FROM tasks t
        LEFT JOIN columns c ON c.id = t.column_id
        LEFT JOIN boards b ON b.id = t.board_id
        WHERE t.project_id = ? AND t.column_id IN ({placeholders})
          AND COALESCE(t.archived, 0) = 0
    """
    params: List[Any] = [pid, *stage_ids]
    if board_id is not None:
        sql += " AND t.board_id = ?"
        params.append(board_id)
    if priorities:
        ph = ",".join("?" for _ in priorities)
        sql += f" AND t.priority IN ({ph})"
        params.extend(priorities)
    sql += " ORDER BY t.updated_at ASC"
    with db_cursor() as (cur, conn):
        cur.execute(sql, params)
        rows = [row_to_dict(r) for r in cur.fetchall()]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [r for r in rows if _parse_dt(r["updated_at"]) <= cutoff]


def analyze_next(
    tasks: List[Dict[str, Any]], active_ids: Optional[List[int]] = None
) -> Dict[str, Any]:
    """Suggest the next task, preferring the active stages when known."""
    if active_ids:
        candidates = [t for t in tasks if t.get("column_id") in active_ids]
    else:
        # Backwards-compatible fallback for callers without project stages.
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


def review_stage_id(project_id: Optional[int] = None) -> Optional[int]:
    """Return the review stage: the third column by position.

    Falls back to the last column when a project has fewer than three stages.
    """
    cols = list_columns(project_id)
    if not cols:
        return None
    return cols[2]["id"] if len(cols) >= 3 else cols[-1]["id"]


def list_review_tasks(
    project_id: Optional[int] = None, board_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Tasks waiting for the owner's review, oldest activity first."""
    pid = _default_project_id(project_id)
    cid = review_stage_id(pid)
    if cid is None:
        return []
    tasks = list_tasks(project_id=pid, column_id=cid, board_id=board_id)
    tasks.sort(key=lambda t: t.get("updated_at") or "")
    return tasks


def reject_task(
    task_id: int,
    reason: str,
    actor: str = "user",
    author: Optional[str] = None,
    return_to_column_id: Optional[int] = None,
    images: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Reject a task: count the rejection, move it back, and comment why.

    By default the task moves one stage back (the previous column). The
    rejection reason is required and becomes a comment, so agents see it.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Rejection reason is required")
    task = get_task(task_id)
    if not task:
        return None
    if task.get("archived"):
        raise ValueError("Archived task cannot be rejected")
    if return_to_column_id is None:
        nav = _stage_navigation(task)
        previous = nav["previous"]
        if previous is None:
            raise ValueError("Cannot reject a task from the first stage")
        return_to_column_id = previous["id"]
    if get_column_by_id(return_to_column_id, project_id=task["project_id"]) is None:
        raise ValueError("Target column does not belong to the task project")
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            """UPDATE tasks
               SET rejections = COALESCE(rejections, 0) + 1, updated_at = ?
               WHERE id = ?""",
            (ts, task_id),
        )
        history_service.record_event(
            task_id,
            "rejected",
            old_value=str(task["column_id"]),
            new_value=str(return_to_column_id),
            actor=actor,
            note=reason,
            created_at=ts,
            cur=cur,
        )
    if return_to_column_id != task["column_id"]:
        move_task(task_id, return_to_column_id, actor=actor, note=reason)
    comment_service.add_comment(
        task_id,
        author or ("Agent:" + actor if actor.startswith("agent:") else actor),
        f"Отклонено: {reason}",
        touch_task=False,
        kind="reject",
        images=images,
    )
    return get_task(task_id)


def verify_task(
    task_id: int,
    actor: str = "user",
    note: str = "",
    move_to_next: bool = True,
    author: Optional[str] = None,
    images: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Mark a task as verified by the owner and optionally move it forward."""
    task = get_task(task_id)
    if not task:
        return None
    if task.get("archived"):
        raise ValueError("Archived task cannot be verified")
    note = (note or "").strip()
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE tasks SET verified_at = ?, verified_by = ?, updated_at = ? WHERE id = ?",
            (ts, actor, ts, task_id),
        )
        history_service.record_event(
            task_id,
            "verified",
            old_value=None,
            new_value=note or None,
            actor=actor,
            note=note or None,
            created_at=ts,
            cur=cur,
        )
    if move_to_next:
        nav = _stage_navigation(task)
        following = nav["next"]
        if following is not None:
            move_task(task_id, following["id"], actor=actor, note=note or None)
    comment_service.add_comment(
        task_id,
        author or ("Agent:" + actor if actor.startswith("agent:") else actor),
        f"Проверено: {note}" if note else "Проверено.",
        touch_task=False,
        kind="verify",
        images=images,
    )
    return get_task(task_id)


def archive_task(task_id: int, actor: str = "user") -> Optional[Dict[str, Any]]:
    """Hide a task from the board without deleting it."""
    task = get_task(task_id)
    if not task:
        return None
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE tasks SET archived = 1, archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, task_id),
        )
        history_service.record_event(
            task_id,
            "archived",
            old_value=None,
            new_value=None,
            actor=actor,
            created_at=ts,
            cur=cur,
        )
    return get_task(task_id)


def restore_task(task_id: int, actor: str = "user") -> Optional[Dict[str, Any]]:
    """Bring an archived task back to the board."""
    task = get_task(task_id)
    if not task:
        return None
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE tasks SET archived = 0, archived_at = NULL, updated_at = ? WHERE id = ?",
            (ts, task_id),
        )
        history_service.record_event(
            task_id,
            "restored",
            old_value=None,
            new_value=None,
            actor=actor,
            created_at=ts,
            cur=cur,
        )
    return get_task(task_id)


def set_acceptance_criteria(
    task_id: int, criteria, actor: str = "user"
) -> Optional[Dict[str, Any]]:
    """Replace a task's acceptance criteria, preserving marks by exact text."""
    task = get_task(task_id)
    if not task:
        return None
    old_by_text = {
        c["text"]: c for c in _parse_criteria(task.get("acceptance_criteria"))
    }
    normalized = []
    for item in _parse_criteria(criteria):
        previous = old_by_text.get(item["text"])
        if previous:
            item["done"] = bool(item.get("done") or previous.get("done"))
            item["evidence"] = item.get("evidence") or previous.get("evidence") or ""
            item["checked_at"] = previous.get("checked_at")
            item["checked_by"] = previous.get("checked_by")
        normalized.append(item)
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE tasks SET acceptance_criteria = ?, updated_at = ? WHERE id = ?",
            (_dump_criteria(normalized), ts, task_id),
        )
        history_service.record_event(
            task_id,
            "criteria_updated",
            old_value=None,
            new_value=f"{len(normalized)} criteria",
            actor=actor,
            created_at=ts,
            cur=cur,
        )
    return get_task(task_id)


def check_criterion(
    task_id: int,
    index: int,
    evidence: str = "",
    done: bool = True,
    actor: str = "user",
) -> Optional[Dict[str, Any]]:
    """Mark acceptance criterion ``index`` (1-based) as done or not done.

    Completing a criterion requires evidence; this keeps "done" verifiable.
    """
    task = get_task(task_id)
    if not task:
        return None
    criteria = _parse_criteria(task.get("acceptance_criteria"))
    if index < 1 or index > len(criteria):
        raise ValueError(
            f"Criterion index {index} is out of range (1..{len(criteria)})"
        )
    evidence = (evidence or "").strip()
    if done and not evidence:
        raise ValueError("Evidence is required to complete an acceptance criterion")
    item = criteria[index - 1]
    item["done"] = bool(done)
    item["evidence"] = evidence
    item["checked_at"] = now() if done else None
    item["checked_by"] = actor if done else None
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE tasks SET acceptance_criteria = ?, updated_at = ? WHERE id = ?",
            (_dump_criteria(criteria), ts, task_id),
        )
        history_service.record_event(
            task_id,
            "criteria_checked",
            old_value=None,
            new_value=item["text"],
            actor=actor,
            note=evidence or None,
            created_at=ts,
            cur=cur,
        )
    return get_task(task_id)
