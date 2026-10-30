import json
import os
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from db.database import init_db
from models.schemas import TaskUpdate
from services import (
    task_service,
    comment_service,
    settings_service,
    agent_service,
    project_service,
    history_service,
)

MCP_INSTRUCTIONS = """
Local Kanban task-board MCP server.

IMPORTANT: use these MCP tools directly for all board operations. Do NOT call
the REST API (/api/..., /openapi.json) and do NOT use fetch/HTTP requests to
read or modify tasks. The project is already selected by the MCP URL query
parameter `?project=<slug>`; never guess a project id.

Typical flow:
- register_agent once per session;
- get_board or get_columns to inspect the board;
- get_tasks / get_task to read tasks;
- create_task to add tasks to Backlog;
- update_task for title/description/category/priority/affected_files;
- add_comment_to_task for progress notes;
- get_task_history to inspect the timeline;
- request_move_task to move a task. The server enforces adjacent stages only
  (previous/next). Without explicit user approval the tool returns
  ACTION_REQUIRED; ask the user before passing user_explicitly_approved=true;
- delete_task only after explicit user confirmation, with
  user_explicitly_approved=true.
"""

mcp = FastMCP(name="KanbanBoardMCP", instructions=MCP_INSTRUCTIONS)


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
def get_columns(project: Optional[str] = None) -> List[dict]:
    """Return the ordered workflow columns of the current project."""
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    return task_service.list_columns(project_id=proj["id"])


@mcp.tool
def get_board(project: Optional[str] = None) -> dict:
    """Return project info, ordered columns and all tasks in one call.

    Use this instead of fetching the REST API when you need the full board.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    cols = task_service.list_columns(project_id=proj["id"])
    tasks = task_service.list_tasks(project_id=proj["id"])
    for t in tasks:
        t["comment_count"] = len(comment_service.list_comments(t["id"]))
        t["project"] = proj["slug"]
    return {
        "project": {
            "id": proj["id"],
            "name": proj["name"],
            "slug": proj["slug"],
            "description": proj.get("description", ""),
        },
        "columns": cols,
        "tasks": tasks,
    }


@mcp.tool
def get_task(task_id: int, project: Optional[str] = None) -> dict:
    """Return one task with metadata, comments count and project slug."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    t["project"] = proj["slug"]
    return t


@mcp.tool
def update_task(
    agent_id: str,
    task_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    affected_files: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """Update task fields. Status changes must go through request_move_task."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    agent_service.touch_agent(agent_id)
    try:
        updated = task_service.update_task(
            task_id,
            TaskUpdate(
                title=title,
                description=description,
                category=category,
                priority=priority,
                affected_files=affected_files,
            ),
            actor=f"agent:{agent_id}",
        )
    except ValueError as e:
        return {"error": str(e)}
    if updated:
        updated["project"] = proj["slug"]
    return updated


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
def delete_task(
    agent_id: str,
    task_id: int,
    user_explicitly_approved: bool = False,
    reason: str = "",
    project: Optional[str] = None,
) -> dict:
    """Permanently delete a task after explicit user confirmation.

    Set ``user_explicitly_approved=True`` only when the user explicitly asked
    to delete this exact task. Never use REST DELETE endpoints directly.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    agent_service.touch_agent(agent_id)
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    if not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": (
                f"Удаление задачи необратимо. Спроси пользователя: "
                f"'Удалить задачу #{task_id} ({t['title']})?'"
            ),
        }
    history_service.record_event(
        task_id,
        "deleted",
        old_value=str(t.get("column_id")),
        actor=f"agent:{agent_id}",
        note=(reason or "").strip() or None,
    )
    deleted = task_service.delete_task(task_id)
    return {
        "status": "deleted" if deleted else "not_found",
        "task_id": task_id,
        "title": t["title"],
    }


@mcp.tool
def request_move_task(
    agent_id: str,
    task_id: int,
    target_column: str,
    user_explicitly_approved: bool = False,
    reason: str = "",
    project: Optional[str] = None,
) -> dict:
    """Move a task to an adjacent column only.

    Adjacent means the previous or next stage in the project's ordered column
    list. ``reason`` is optional and, when the move succeeds, is used in an
    automatic agent comment and stored in the task history.
    """
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

    reason = (reason or "").strip()
    old_col = task_service.get_column_by_id(t["column_id"], project_id=proj["id"])
    old_name = old_col["name"] if old_col else f"Колонка #{t['column_id']}"

    try:
        updated = task_service.move_task(
            task_id, cid, actor=f"agent:{agent_id}", note=reason or None
        )
    except ValueError as e:
        return {"error": str(e)}

    moved = bool(updated and updated["column_id"] == cid and t["column_id"] != cid)
    if moved:
        clean_reason = reason.rstrip(" .!?;:,")
        if clean_reason:
            comment_text = (
                f"Я переместил эту задачу из «{old_name}» в «{col_name}», "
                f"так как {clean_reason}."
            )
        else:
            comment_text = f"Я переместил эту задачу из «{old_name}» в «{col_name}»."
        comment_service.add_comment(task_id, f"Agent:{agent_id}", comment_text)
        history_service.record_event(
            task_id,
            "note",
            old_value=None,
            new_value=comment_text,
            actor=f"agent:{agent_id}",
            note=reason or None,
        )
        updated = task_service.get_task(task_id)

    return {"status": "moved" if moved else "unchanged", "task": updated}


@mcp.tool
def get_task_history(task_id: int, project: Optional[str] = None) -> List[dict]:
    """Return the chronological audit history of a task in the current project."""
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    t = task_service.get_task(task_id)
    if not t:
        return [{"error": f"Task {task_id} not found"}]
    if t.get("project_id") != proj["id"]:
        return [{"error": f"Task {task_id} not in project {proj['slug']}"}]
    return history_service.get_task_history(task_id)
