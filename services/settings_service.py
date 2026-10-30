from typing import List, Dict, Any, Optional
from db.database import db_cursor, row_to_dict


def list_settings() -> List[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM mcp_settings ORDER BY key")
        rows = [row_to_dict(r) for r in cur.fetchall()]
    for r in rows:
        r["is_enabled"] = bool(r["is_enabled"])
    return rows


def get_setting(key: str) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM mcp_settings WHERE key = ?", (key,))
        r = cur.fetchone()
        if not r:
            return None
        d = row_to_dict(r)
        d["is_enabled"] = bool(d["is_enabled"])
        return d


def update_setting(
    key: str, value: Optional[str] = None, is_enabled: Optional[bool] = None
) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM mcp_settings WHERE key = ?", (key,))
        if not cur.fetchone():
            return None
        if value is not None:
            cur.execute("UPDATE mcp_settings SET value = ? WHERE key = ?", (value, key))
        if is_enabled is not None:
            cur.execute(
                "UPDATE mcp_settings SET is_enabled = ? WHERE key = ?",
                (1 if is_enabled else 0, key),
            )
    return get_setting(key)


def is_enabled(key: str) -> bool:
    s = get_setting(key)
    if not s:
        return True
    return bool(s["is_enabled"]) and s.get("value", "true").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
