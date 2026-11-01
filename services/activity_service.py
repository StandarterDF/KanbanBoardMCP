"""Unified board activity feed.

Merges the audit trail (creation, moves, field updates) with task comments so
an agent can ask "what changed since X?" in one call. Move notes that were
auto-created together with a comment are not duplicated.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from db.database import db_cursor, row_to_dict


def _since_iso(since: Optional[str], default_hours: int = 24) -> str:
    if since and str(since).strip():
        return str(since).strip()
    ts = datetime.now(timezone.utc) - timedelta(hours=default_hours)
    return ts.isoformat(timespec="seconds").replace("+00:00", "Z")


def get_activity(
    project_id: int,
    board_id: Optional[int] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return a chronological activity feed for one project (or board).

    ``since`` is an ISO timestamp; when omitted the last 24 hours are used.
    ``limit`` keeps the newest ``limit`` items (still returned oldest-first).
    """
    since_iso = _since_iso(since)

    with db_cursor() as (cur, conn):
        sql = """
            SELECT al.id, al.task_id, al.action, al.old_value, al.new_value,
                   al.actor, al.note, al.created_at,
                   t.title AS task_title, t.board_id,
                   co.name AS old_column_name, cn.name AS new_column_name
            FROM audit_log al
            JOIN tasks t ON t.id = al.task_id
            LEFT JOIN columns co
                ON al.action IN ('moved', 'created')
               AND co.id = CAST(REPLACE(COALESCE(al.old_value, ''), 'column_id=', '') AS INTEGER)
            LEFT JOIN columns cn
                ON al.action IN ('moved', 'created')
               AND cn.id = CAST(REPLACE(COALESCE(al.new_value, ''), 'column_id=', '') AS INTEGER)
            WHERE t.project_id = ? AND al.created_at >= ?
        """
        params: List[Any] = [project_id, since_iso]
        if board_id is not None:
            sql += " AND t.board_id = ?"
            params.append(board_id)
        cur.execute(sql, params)
        events = [row_to_dict(r) for r in cur.fetchall()]

        sql = """
            SELECT cm.id, cm.task_id, cm.author, cm.text, cm.kind, cm.created_at,
                   t.title AS task_title, t.board_id
            FROM comments cm
            JOIN tasks t ON t.id = cm.task_id
            WHERE t.project_id = ? AND cm.created_at >= ?
        """
        params = [project_id, since_iso]
        if board_id is not None:
            sql += " AND t.board_id = ?"
            params.append(board_id)
        cur.execute(sql, params)
        comments = [row_to_dict(r) for r in cur.fetchall()]

    items: List[Dict[str, Any]] = []
    note_texts = {
        (e["task_id"], e["new_value"])
        for e in events
        if e.get("action") == "note" and e.get("new_value")
    }

    for e in events:
        items.append(
            {
                "ref": f"event:{e['id']}",
                "kind": "event",
                "id": e["id"],
                "task_id": e["task_id"],
                "task_title": e.get("task_title"),
                "board_id": e.get("board_id"),
                "action": e.get("action"),
                "actor": e.get("actor") or "system",
                "text": e.get("new_value"),
                "note": e.get("note"),
                "old_value": e.get("old_value"),
                "new_value": e.get("new_value"),
                "old_column_name": e.get("old_column_name"),
                "new_column_name": e.get("new_column_name"),
                "created_at": e["created_at"],
            }
        )

    for c in comments:
        if (c["task_id"], c["text"]) in note_texts:
            continue
        items.append(
            {
                "ref": f"comment:{c['id']}",
                "kind": "comment",
                "comment_kind": c.get("kind") or "message",
                "id": c["id"],
                "task_id": c["task_id"],
                "task_title": c.get("task_title"),
                "board_id": c.get("board_id"),
                "action": "comment",
                "actor": c.get("author") or "system",
                "text": c.get("text"),
                "note": None,
                "old_value": None,
                "new_value": None,
                "old_column_name": None,
                "new_column_name": None,
                "created_at": c["created_at"],
            }
        )

    items.sort(
        key=lambda i: (i.get("created_at") or "", i["kind"] != "event", i["ref"])
    )
    if limit is not None and limit > 0:
        items = items[-limit:]
    return items
