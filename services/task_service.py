import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from db.database import db_cursor, now, row_to_dict
from models.schemas import Task, TaskCreate, TaskUpdate


PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def list_columns() -> List[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM columns ORDER BY order_index")
        return [row_to_dict(r) for r in cur.fetchall()]


def get_column_by_name(name: str) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM columns WHERE name = ?", (name,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def get_column_by_id(cid: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM columns WHERE id = ?", (cid,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def list_tasks(
    column_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    priority: Optional[str] = None,
) -> List[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        sql = """
            SELECT t.*, c.name AS column_name,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.task_id = t.id) AS comment_count
            FROM tasks t LEFT JOIN columns c ON c.id = t.column_id
            WHERE 1=1
        """
        params: list = []
        if column_id is not None:
            sql += " AND t.column_id = ?"
            params.append(column_id)
        if agent_id is not None:
            sql += " AND t.agent_id = ?"
            params.append(agent_id)
        if priority is not None:
            sql += " AND t.priority = ?"
            params.append(priority)
        sql += " ORDER BY t.id DESC"
        cur.execute(sql, params)
        return [row_to_dict(r) for r in cur.fetchall()]


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute(
            """
            SELECT t.*, c.name AS column_name,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.task_id = t.id) AS comment_count
            FROM tasks t LEFT JOIN columns c ON c.id = t.column_id
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
) -> Dict[str, Any]:
    if column_id is None:
        col = get_column_by_name("Backlog")
        column_id = col["id"] if col else 1
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            """
            INSERT INTO tasks (title, description, category, priority, column_id, agent_id, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
        """,
            (title, description, category, priority, column_id, agent_id, ts, ts),
        )
        tid = cur.lastrowid
        cur.execute(
            "INSERT INTO audit_log (task_id, action, new_value, actor, created_at) VALUES (?,?,?,?,?)",
            (tid, "created", f"column_id={column_id}", agent_id or "user", ts),
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
        fields["category"] = data.category
    if getattr(data, "priority", None) is not None:
        fields["priority"] = data.priority
    if getattr(data, "column_id", None) is not None:
        fields["column_id"] = data.column_id
    if not fields:
        return task
    ts = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [ts, task_id]
    with db_cursor() as (cur, conn):
        if "column_id" in fields and fields["column_id"] != task["column_id"]:
            cur.execute(
                "INSERT INTO audit_log (task_id, action, old_value, new_value, actor, created_at) VALUES (?,?,?,?,?,?)",
                (
                    task_id,
                    "moved",
                    str(task["column_id"]),
                    str(fields["column_id"]),
                    actor,
                    ts,
                ),
            )
        cur.execute(f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?", params)
    return get_task(task_id)


def move_task(
    task_id: int, target_column_id: int, actor: str = "user"
) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task:
        return None
    if task["column_id"] == target_column_id:
        return task
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "INSERT INTO audit_log (task_id, action, old_value, new_value, actor, created_at) VALUES (?,?,?,?,?,?)",
            (
                task_id,
                "moved",
                str(task["column_id"]),
                str(target_column_id),
                actor,
                ts,
            ),
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


def stalled_tasks(hours: int = 24) -> List[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("""
            SELECT t.*, c.name AS column_name,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.task_id = t.id) AS comment_count
            FROM tasks t LEFT JOIN columns c ON c.id = t.column_id
            WHERE t.priority IN ('High','Critical')
              AND c.name IN ('Backlog','In Progress')
            ORDER BY t.updated_at ASC
        """)
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
