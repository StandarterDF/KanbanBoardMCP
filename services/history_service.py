"""Task audit history helpers.

Events are stored in ``audit_log``. Column ids are resolved to human readable
names on read, so history stays valid even after a column is renamed. Deleted
columns fall back to ``Колонка #<id>``.
"""

import json
from typing import Any, Dict, List, Optional

from db.database import db_cursor, now, row_to_dict

MOVE_ACTIONS = {"moved", "rejected"}
CREATE_ACTIONS = {"created"}


def _column_id_from_value(value: Any) -> Optional[int]:
    """Extract a column id from legacy (``column_id=6``) or plain (``6``) values."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("column_id="):
        s = s.split("=", 1)[1].strip()
    if s.isdigit():
        return int(s)
    return None


def record_event(
    task_id: int,
    action: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    actor: str = "system",
    note: Optional[str] = None,
    created_at: Optional[str] = None,
    cur=None,
) -> None:
    """Append an audit event.

    Pass ``cur`` to reuse an existing transaction (the caller owns the commit).
    """
    ts = created_at or now()
    params = (task_id, action, old_value, new_value, actor, note, ts)
    sql = (
        "INSERT INTO audit_log (task_id, action, old_value, new_value, actor, note, created_at) "
        "VALUES (?,?,?,?,?,?,?)"
    )
    if cur is not None:
        cur.execute(sql, params)
        return
    with db_cursor() as (c, conn):
        c.execute(sql, params)


def _columns_map(cur) -> Dict[int, str]:
    cur.execute("SELECT id, name FROM columns")
    return {r["id"]: r["name"] for r in cur.fetchall()}


def _column_name(col_id: Optional[int], names: Dict[int, str]) -> Optional[str]:
    if col_id is None:
        return None
    return names.get(col_id, f"Колонка #{col_id}")


def get_task_history(task_id: int) -> List[Dict[str, Any]]:
    """Return chronological history events for a task, with resolved column names."""
    with db_cursor() as (cur, conn):
        names = _columns_map(cur)
        cur.execute(
            "SELECT * FROM audit_log WHERE task_id = ? ORDER BY created_at ASC, id ASC",
            (task_id,),
        )
        rows = [row_to_dict(r) for r in cur.fetchall()]

    events: List[Dict[str, Any]] = []
    for row in rows:
        action = row.get("action") or ""
        old_col = _column_id_from_value(row.get("old_value"))
        new_col = _column_id_from_value(row.get("new_value"))

        # Only treat values as columns for movement/creation events, not for
        # "updated" events where the value is a JSON payload.
        if action not in MOVE_ACTIONS | CREATE_ACTIONS:
            old_col = None
            new_col = None

        events.append(
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "action": action,
                "old_value": row.get("old_value"),
                "new_value": row.get("new_value"),
                "actor": row.get("actor") or "system",
                "note": row.get("note"),
                "created_at": row["created_at"],
                "old_column_id": old_col,
                "new_column_id": new_col,
                "old_column_name": _column_name(old_col, names),
                "new_column_name": _column_name(new_col, names),
            }
        )
    return events


def dump_fields(fields: Dict[str, Any]) -> str:
    """Serialize a field snapshot for ``updated`` audit events."""
    return json.dumps(fields, ensure_ascii=False, default=str)
