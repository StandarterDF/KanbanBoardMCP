from typing import List, Dict, Any, Optional
from db.database import db_cursor, now, row_to_dict


def list_comments(task_id: int) -> List[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute(
            "SELECT * FROM comments WHERE task_id = ? ORDER BY id ASC", (task_id,)
        )
        return [row_to_dict(r) for r in cur.fetchall()]


def add_comment(task_id: int, author: str, text: str) -> Optional[Dict[str, Any]]:
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "INSERT INTO comments (task_id, author, text, created_at) VALUES (?,?,?,?)",
            (task_id, author, text, ts),
        )
        cid = cur.lastrowid
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM comments WHERE id = ?", (cid,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None
