from typing import List, Dict, Any, Optional
import json

from db.database import db_cursor, now, row_to_dict


REVIEW_KINDS = ("verify", "reject")
COMMENT_KINDS = ("message",) + REVIEW_KINDS

MAX_IMAGES_PER_COMMENT = 10
MAX_IMAGE_URL_LENGTH = 2048


def _normalize_images(images: Optional[List[str]]) -> str:
    """Keep only safe attachment URLs, stored as a JSON array."""
    if not images:
        return "[]"
    clean: List[str] = []
    for url in images:
        if not isinstance(url, str):
            continue
        url = url.strip()
        if not url or len(url) > MAX_IMAGE_URL_LENGTH:
            continue
        if not (
            url.startswith("/static/uploads/")
            or url.startswith(("http://", "https://"))
        ):
            continue
        if url not in clean:
            clean.append(url)
        if len(clean) >= MAX_IMAGES_PER_COMMENT:
            break
    return json.dumps(clean, ensure_ascii=False)


def _comment_to_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = row_to_dict(row)
    try:
        images = json.loads(data.get("images") or "[]")
    except (TypeError, ValueError):
        images = []
    data["images"] = images if isinstance(images, list) else []
    return data


def list_comments(
    task_id: int,
    since_id: Optional[int] = None,
    limit: Optional[int] = None,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return task comments in chronological order.

    ``since_id`` returns only comments newer than that id (incremental reads).
    ``limit`` keeps the **last** N comments (for long threads).
    ``kind`` filters by comment type: ``message`` for plain discussion,
    ``verify``/``reject`` for owner review decisions.
    """
    sql = "SELECT * FROM comments WHERE task_id = ?"
    params: List[Any] = [task_id]
    if since_id is not None:
        sql += " AND id > ?"
        params.append(int(since_id))
    if kind:
        sql += " AND kind = ?"
        params.append(str(kind))
    sql += " ORDER BY id ASC"
    if limit is not None and limit > 0:
        # newest N, still returned chronologically
        sql = (
            "SELECT * FROM ("
            + sql.replace(" ORDER BY id ASC", " ORDER BY id DESC")
            + " LIMIT ?) ORDER BY id ASC"
        )
        params.append(int(limit))
    with db_cursor() as (cur, conn):
        cur.execute(sql, params)
        return [_comment_to_dict(r) for r in cur.fetchall()]


def get_last_review_comment(task_id: int) -> Optional[Dict[str, Any]]:
    """Return the most recent review decision comment (verify or reject).

    Review decisions are terminal: they always describe the latest owner
    verdict, while the rest of the thread is normal discussion. Agents can
    read this single record instead of scanning every comment.
    """
    with db_cursor() as (cur, conn):
        cur.execute(
            """SELECT * FROM comments
               WHERE task_id = ? AND kind IN (?, ?)
               ORDER BY id DESC LIMIT 1""",
            (task_id, *REVIEW_KINDS),
        )
        r = cur.fetchone()
        return _comment_to_dict(r) if r else None


def add_comment(
    task_id: int,
    author: str,
    text: str,
    touch_task: bool = True,
    kind: str = "message",
    images: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Add a comment and bump the task's ``updated_at``.

    Bumping ``updated_at`` makes a new requirement show up as recent activity:
    the card stops looking "stalled" and activity-based ordering sees it.
    ``kind`` is ``message`` for discussion, ``verify``/``reject`` for owner
    review decisions.
    """
    if kind not in COMMENT_KINDS:
        kind = "message"
    ts = now()
    images_json = _normalize_images(images)
    with db_cursor() as (cur, conn):
        cur.execute(
            """INSERT INTO comments (task_id, author, text, kind, created_at, images)
               VALUES (?,?,?,?,?,?)""",
            (task_id, author, text, kind, ts, images_json),
        )
        cid = cur.lastrowid
        if touch_task:
            cur.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ? AND updated_at < ?",
                (ts, task_id, ts),
            )
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM comments WHERE id = ?", (cid,))
        r = cur.fetchone()
        return _comment_to_dict(r) if r else None
