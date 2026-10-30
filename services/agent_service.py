import json
from typing import Dict, Any, Optional
from db.database import db_cursor, now, row_to_dict


def register_agent(
    agent_id: str, name: str, metadata: Optional[dict] = None
) -> Dict[str, Any]:
    ts = now()
    meta = json.dumps(metadata or {})
    with db_cursor() as (cur, conn):
        cur.execute(
            """
            INSERT INTO agent_sessions (agent_id, name, last_active, metadata)
            VALUES (?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET name=excluded.name, last_active=excluded.last_active, metadata=excluded.metadata
        """,
            (agent_id, name, ts, meta),
        )
    return {
        "agent_id": agent_id,
        "name": name,
        "last_active": ts,
        "metadata": metadata or {},
    }


def touch_agent(agent_id: str):
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE agent_sessions SET last_active = ? WHERE agent_id = ?",
            (ts, agent_id),
        )


def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM agent_sessions WHERE agent_id = ?", (agent_id,))
        r = cur.fetchone()
        if not r:
            return None
        d = row_to_dict(r)
        try:
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
        except Exception:
            d["metadata"] = {}
        return d
