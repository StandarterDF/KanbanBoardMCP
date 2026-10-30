import json
import os
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from db.database import init_db
from services import (
    task_service,
    comment_service,
    settings_service,
    agent_service,
    project_service,
)

mcp = FastMCP(name="KanbanBoardMCP", instructions="Local Kanban task board MCP server.")


def _resolve_project(override: Optional[str] = None) -> (Dict[str, Any], Optional[str]):
    slug = override
    if not slug:
        try:
            req = get_http_request()
            slug = req.query_params.get("project")
        except Exception:
            slug = os.environ.get("KANBAN_PROJECT")
    if not slug:
        slug = "default"
    proj = project_service.get_project_by_slug(slug)
    if not proj:
        return None, f"Project not found: {slug}"
    return proj, None


def _col_id(target, project_id: int) -> Optional[int]:
    if target is None:
        return None
    if isinstance(target, int):
        col = task_service.get_column_by_id(target, project_id=project_id)
        return col["id"] if col else None
    s = str(target).strip()
    if s.isdigit():
        col = task_service.get_column_by_id(int(s), project_id=project_id)
        return col["id"] if col else None
    col = task_service.get_column_by_name(s, project_id=project_id)
    return col["id"] if col else None


def _guard_setting(key: str) -> Optional[str]:
    if not settings_service.is_enabled(key):
        return f"Feature disabled by user setting: {key}"
    return None


@mcp.tool
def register_agent(agent_id: str, name: str, project: Optional[str] = None) -> dict:
    """Register an AI agent in the Kanban system."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    return agent_service.register_agent(agent_id, name)


@mcp.tool
def get_tasks(
    agent_id: Optional[str] = None,
    column_id: Optional[str] = None,
    priority: Optional[str] = None,
    project: Optional[str] = None,
) -> List[dict]:
    """Get tasks with optional filters. column_id can be id or name. Project from URL/env/default."""
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    agent_service.touch_agent(agent_id) if agent_id else None
    cid = None
    if column_id is not None:
        cid = _col_id(column_id, proj["id"])
        if cid is None:
            return [{"error": f"Unknown column: {column_id}"}]
    tasks = task_service.list_tasks(
        project_id=proj["id"], column_id=cid, agent_id=agent_id, priority=priority
    )
    out = []
    for t in tasks:
        comments = comment_service.list_comments(t["id"])
        t = dict(t)
        t["comment_count"] = len(comments)
        t["project"] = proj["slug"]
        out.append(t)
    return out


@mcp.tool
def create_task(
    agent_id: str,
    title: str,
    description: str,
    category: str,
    priority: str,
    affected_files: Optional[str] = "",
    column_id: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """Create a structured task. Project from URL/env/default."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    msg = _guard_setting("allow_agent_create_tasks")
    if msg:
        return {"error": msg}
    agent_service.touch_agent(agent_id)
    cid = None
    if column_id is not None:
        cid = _col_id(column_id, proj["id"])
        if cid is None:
            return {"error": f"Unknown column: {column_id}"}
    try:
        t = task_service.create_task(
            title=title,
            description=description,
            category=category,
            priority=priority,
            column_id=cid,
            agent_id=agent_id,
            affected_files=affected_files or "",
            project_id=proj["id"],
        )
    except ValueError as e:
        return {"error": str(e)}
    return t


@mcp.tool
def analyze_and_suggest_next_task(agent_id: str, project: Optional[str] = None) -> dict:
    """Analyze tasks and suggest the next one to work on."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    agent_service.touch_agent(agent_id)
    tasks = task_service.list_tasks(project_id=proj["id"])
    analysis = task_service.analyze_next(tasks)
    stalled = []
    if settings_service.is_enabled("enable_proactive_notifications"):
        stalled = task_service.stalled_tasks(hours=24, project_id=proj["id"])
    return {
        "suggestion": analysis,
        "stalled_tasks": stalled,
        "total_tasks": len(tasks),
        "project": proj["slug"],
    }


@mcp.tool
def add_comment_to_task(
    agent_id: str, task_id: int, comment_text: str, project: Optional[str] = None
) -> dict:
    """Add a comment to a task on behalf of the agent."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    msg = _guard_setting("allow_agent_comment")
    if msg:
        return {"error": msg}
    agent_service.touch_agent(agent_id)
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    # ensure task belongs to project
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    c = comment_service.add_comment(task_id, f"Agent:{agent_id}", comment_text)
    return c


@mcp.tool
def request_move_task(
    agent_id: str,
    task_id: int,
    target_column: str,
    user_explicitly_approved: bool = False,
    project: Optional[str] = None,
) -> dict:
    """Move a task to another column with movement guard."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    agent_service.touch_agent(agent_id)
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    cid = _col_id(target_column, proj["id"])
    if cid is None:
        return {"error": f"Unknown target column: {target_column}"}
    col = task_service.get_column_by_id(cid, project_id=proj["id"])
    col_name = col["name"] if col else str(target_column)

    strict = settings_service.is_enabled("strict_move_confirmation")
    if strict and not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": f"Требуется подтверждение владельца. Задай пользователю вопрос: 'Я завершил работу над задачей #{task_id} ({t['title']}). Переместить её в колонку {col_name}?'",
        }

    try:
        updated = task_service.move_task(task_id, cid, actor=f"agent:{agent_id}")
    except ValueError as e:
        return {"error": str(e)}
    return {"status": "moved", "task": updated}
