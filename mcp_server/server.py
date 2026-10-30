import json
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP

from db.database import init_db
from services import task_service, comment_service, settings_service, agent_service

mcp = FastMCP(name="KanbanBoardMCP", instructions="Local Kanban task board MCP server.")


def _col_id(target) -> Optional[int]:
    if target is None:
        return None
    if isinstance(target, int):
        return target
    s = str(target).strip()
    if s.isdigit():
        return int(s)
    col = task_service.get_column_by_name(s)
    return col["id"] if col else None


def _guard_setting(key: str) -> Optional[str]:
    if not settings_service.is_enabled(key):
        return f"Feature disabled by user setting: {key}"
    return None


@mcp.tool
def register_agent(agent_id: str, name: str) -> dict:
    """Register an AI agent in the Kanban system."""
    return agent_service.register_agent(agent_id, name)


@mcp.tool
def get_tasks(
    agent_id: Optional[str] = None,
    column_id: Optional[str] = None,
    priority: Optional[str] = None,
) -> List[dict]:
    """Get tasks with optional filters. column_id can be id or name."""
    agent_service.touch_agent(agent_id) if agent_id else None
    cid = _col_id(column_id)
    tasks = task_service.list_tasks(column_id=cid, agent_id=agent_id, priority=priority)
    # enrich
    out = []
    for t in tasks:
        comments = comment_service.list_comments(t["id"])
        t = dict(t)
        t["comment_count"] = len(comments)
        out.append(t)
    return out


@mcp.tool
def create_task(
    agent_id: str, title: str, description: str, category: str, priority: str
) -> dict:
    """Create a structured task in Backlog."""
    msg = _guard_setting("allow_agent_create_tasks")
    if msg:
        return {"error": msg}
    agent_service.touch_agent(agent_id)
    t = task_service.create_task(
        title=title,
        description=description,
        category=category,
        priority=priority,
        agent_id=agent_id,
    )
    return t


@mcp.tool
def analyze_and_suggest_next_task(agent_id: str) -> dict:
    """Analyze tasks and suggest the next one to work on."""
    agent_service.touch_agent(agent_id)
    tasks = task_service.list_tasks()
    analysis = task_service.analyze_next(tasks)
    stalled = []
    if settings_service.is_enabled("enable_proactive_notifications"):
        stalled = task_service.stalled_tasks(hours=24)
    return {
        "suggestion": analysis,
        "stalled_tasks": stalled,
        "total_tasks": len(tasks),
    }


@mcp.tool
def add_comment_to_task(agent_id: str, task_id: int, comment_text: str) -> dict:
    """Add a comment to a task on behalf of the agent."""
    msg = _guard_setting("allow_agent_comment")
    if msg:
        return {"error": msg}
    agent_service.touch_agent(agent_id)
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    c = comment_service.add_comment(task_id, f"Agent:{agent_id}", comment_text)
    return c


@mcp.tool
def request_move_task(
    agent_id: str,
    task_id: int,
    target_column: str,
    user_explicitly_approved: bool = False,
) -> dict:
    """Move a task to another column with movement guard."""
    agent_service.touch_agent(agent_id)
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    cid = _col_id(target_column)
    if cid is None:
        return {"error": f"Unknown target column: {target_column}"}
    col = task_service.get_column_by_id(cid)
    col_name = col["name"] if col else str(target_column)

    strict = settings_service.is_enabled("strict_move_confirmation")
    if strict and not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": f"Требуется подтверждение владельца. Задай пользователю вопрос: 'Я завершил работу над задачей #{task_id} ({t['title']}). Переместить её в колонку {col_name}?'",
        }
    # if strict disabled, still require approval? Keep guard always for agents.
    if not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": f"Требуется подтверждение владельца. Задай пользователю вопрос: 'Я завершил работу над задачей #{task_id} ({t['title']}). Переместить её в колонку {col_name}?'",
        }

    updated = task_service.move_task(task_id, cid, actor=f"agent:{agent_id}")
    return {"status": "moved", "task": updated}
