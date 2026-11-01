import json
import os
from datetime import datetime, timezone
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
    category_service,
    boards_service,
    activity_service,
)

MCP_INSTRUCTIONS = """
Local Kanban task-board MCP server.

IMPORTANT: use these MCP tools directly for all board operations. Do NOT call
the REST API (/api/..., /openapi.json) and do NOT use fetch/webfetch or HTTP
requests to read or modify tasks. The project is already selected by the MCP
URL query parameter `?project=<slug>`; never guess a project id. A project can
have several boards; the main board is used by default. The optional
`?board=<id or name>` query parameter (or the `board` argument of a tool)
selects another board. Boards only filter tasks: columns and categories stay
project-wide.

The main board (`is_default=1`) is an aggregate view: it shows EVERY task of the
project and cannot be deleted. Additional boards group tasks and show only their
own tasks. Therefore task creation requires an explicit additional board
whenever the project has one; only a project with just the main board may create
tasks on it. Moving a task to the main board removes it from its group.

Categories are project-scoped. Before creating a task call get_categories (or
get_board) and use one of the category names returned for that project. A task
cannot be created or updated with a category that does not exist in its
project.

Typical flow:
- register_agent once per session;
- get_boards to inspect the project's boards;
- get_board or get_columns to inspect the board (get_board accepts `board`);
- get_categories to inspect the project's categories;
- get_tasks / get_task to read tasks (get_tasks accepts `board`, `q`,
  `categories`, `priorities`, `updated_since`, `sort="activity"`);
- get_work_queue to see the active work stage (usually In Progress) with tasks
  waiting for an answer (`needs_attention`);
- get_stalled_tasks to find High/Critical tasks without recent activity;
- get_activity to poll what changed on the board since a timestamp;
- create_task / bulk_create_tasks to add tasks to Backlog on the selected board;
- assign_task to take a task or clear its assignee (`assignee=""`);
- update_task for title/description/category/priority/affected_files/board;
- move_task_to_board to move a task between boards of the same project;
- add_comment_to_task for progress notes;
- set_acceptance_criteria + check_criterion (evidence required) to make
  "done" verifiable;
- get_review_queue to see tasks waiting for the owner (review stage);
- get_last_review_comment to read only the latest approve/reject verdict;
- verify_task / reject_task for the owner's decision: reject moves the task
  back, increments the rejection counter and writes the reason as a comment;
- archive_task / restore_task to hide finished tasks without deleting them
  (archive requires explicit user approval);
- get_task_history to inspect the timeline;
- request_move_task to move a task. The server enforces adjacent stages only
  (previous/next). Without explicit user approval the tool returns
  ACTION_REQUIRED; ask the user before passing user_explicitly_approved=true;
- bulk_move_tasks to move many tasks to the same adjacent column or to another
  board; non-adjacent tasks are reported in `failed`, not forced.
- delete_task only after explicit user confirmation, with
  user_explicitly_approved=true;
- delete_board / rename_category / delete_category also require explicit user
  confirmation (`user_explicitly_approved=true`); create_board,
  create_category and rename_board are safe.

Comments ARE the task's discussion and the main feedback channel. The owner
usually moves a task to "In Progress" and then writes new requirements as
comments. Therefore:
- get_task returns the full comment thread by default (`include_comments=True`),
  so always re-read the task before starting or finishing work;
- get_tasks returns `comment_count` and a `last_comment` preview for every task;
- read the latest comments first: `get_task_comments(task_id, since_id=...)`
  returns only comments newer than the last id you have seen;
- owner review decisions (approve/reject) are stored as comments with
  `kind="verify"` / `kind="reject"`; `get_last_review_comment(task_id)`
  returns only the latest verdict, while `get_task_comments(kind=...)`
  filters the thread by `message`, `verify` or `reject`;
- write progress/results with add_comment_to_task instead of editing the
  description, and never ignore a newer user comment.
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


def _resolve_board(proj: Dict[str, Any], board: Optional[str] = None):
    """Resolve a board reference (id, name, URL ``?board=`` or default board)."""
    target = board
    if target is None or str(target).strip() == "":
        try:
            req = get_http_request()
            target = req.query_params.get("board")
        except Exception:
            target = os.environ.get("KANBAN_BOARD")
    resolved = boards_service.resolve_board(proj["id"], target)
    if resolved is None:
        return None, f"Board not found in project {proj['slug']}: {target}"
    return resolved, None


def _board_scope(proj: Dict[str, Any], brd: Dict[str, Any]) -> Optional[int]:
    """Task ``board_id`` filter for a board reference.

    The main board is an aggregate view, so it means "no filter" (all tasks of
    the project). Additional boards filter to their own tasks.
    """
    return None if brd.get("is_default") else brd["id"]


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


def _resolve_category(proj: Dict[str, Any], target) -> Optional[Dict[str, Any]]:
    """Resolve a project category by id or case-insensitive name."""
    if target is None:
        return None
    if isinstance(target, int) or str(target).strip().isdigit():
        cat = category_service.get_category(
            target if isinstance(target, int) else int(str(target).strip())
        )
        return cat if cat and cat["project_id"] == proj["id"] else None
    needle = str(target).strip().casefold()
    for cat in category_service.list_categories(project_id=proj["id"]):
        if cat["name"].strip().casefold() == needle:
            return cat
    return None


def _hours_since(timestamp: Optional[str]) -> Optional[float]:
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)


def _guard_setting(key: str) -> Optional[str]:
    if not settings_service.is_enabled(key):
        return f"Feature disabled by user setting: {key}"
    return None


def _last_comment_summary(task: dict, limit: int = 280) -> Optional[dict]:
    """Build the last-comment preview from the SQL comment columns on a task."""
    if not task.get("last_comment_id"):
        return None
    text = task.get("last_comment_text") or ""
    author = task.get("last_comment_author")
    full_length = task.get("last_comment_length")
    truncated = full_length is not None and full_length > len(text)
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
        truncated = True
    return {
        "id": task.get("last_comment_id"),
        "author": author,
        "created_at": task.get("last_comment_at"),
        "text": text,
        "truncated": truncated,
        "kind": task.get("last_comment_kind") or "message",
        # The owner (not an agent) wrote the latest comment: it usually needs
        # to be read before the next action.
        "from_user": bool(author) and not str(author).startswith("Agent:"),
    }


def _stage_info(task: dict) -> dict:
    nav = task_service.get_stage_navigation(task)
    return {
        "current": nav["current"]["name"] if nav["current"] else None,
        "previous": nav["previous"]["name"] if nav["previous"] else None,
        "next": nav["next"]["name"] if nav["next"] else None,
        "allowed_moves": [
            c["name"] for c in (nav["previous"], nav["next"]) if c is not None
        ],
    }


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
    board: Optional[str] = None,
    updated_since: Optional[str] = None,
    q: Optional[str] = None,
    categories: Optional[List[str]] = None,
    priorities: Optional[List[str]] = None,
    sort: str = "id",
    include_last_comment: bool = True,
    include_archived: bool = False,
) -> List[dict]:
    """Get tasks of one board with optional filters.

    ``column_id`` can be id or name. ``board`` can be an id or name; when it is
    omitted the URL ``?board=`` parameter is used, then the main board. The main
    board aggregates tasks from every board of the project; an additional board
    returns only its own tasks.

    Every task carries ``comment_count`` and, unless ``include_last_comment``
    is false, a ``last_comment`` preview. ``last_comment.from_user`` is true
    when the owner wrote the latest message, which usually means new
    requirements are waiting. Use ``get_task`` for the full thread.

    ``updated_since`` is an ISO timestamp (e.g. ``2026-10-31T12:00:00Z``);
    ``sort="activity"`` orders by last update instead of id.

    ``q`` searches title/description/affected files, ``categories`` and
    ``priorities`` filter by lists. ``agent_id`` filters by the task assignee
    (see assign_task), not by the acting agent. Archived tasks are hidden unless
    ``include_archived=True``.
    """
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    brd, err = _resolve_board(proj, board)
    if err:
        return [{"error": err}]
    agent_service.touch_agent(agent_id) if agent_id else None
    cid = None
    if column_id is not None:
        cid = _col_id(column_id, proj["id"])
        if cid is None:
            return [{"error": f"Unknown column: {column_id}"}]
    tasks = task_service.list_tasks(
        project_id=proj["id"],
        column_id=cid,
        agent_id=agent_id,
        priority=priority,
        priorities=priorities,
        categories=categories,
        q=q,
        board_id=_board_scope(proj, brd),
        updated_since=updated_since,
        order_by="activity" if sort == "activity" else "id",
        include_archived=include_archived,
    )
    out = []
    for t in tasks:
        item = dict(t)
        item["project"] = proj["slug"]
        item["board"] = brd["name"]
        if include_last_comment:
            item["last_comment"] = _last_comment_summary(t)
        # Raw helper columns are already represented by last_comment/comment_count.
        for key in (
            "last_comment_at",
            "last_comment_author",
            "last_comment_text",
            "last_comment_length",
            "last_comment_kind",
        ):
            item.pop(key, None)
        out.append(item)
    return out


@mcp.tool
def get_columns(project: Optional[str] = None) -> List[dict]:
    """Return the ordered workflow columns of the current project."""
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    return task_service.list_columns(project_id=proj["id"])


@mcp.tool
def get_categories(project: Optional[str] = None) -> List[dict]:
    """Return the categories of the current project in display order.

    Use one of these names in create_task/update_task; categories are
    project-scoped and cannot be shared between projects.
    """
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    return category_service.list_categories(project_id=proj["id"])


@mcp.tool
def create_category(
    name: str, color: Optional[str] = None, project: Optional[str] = None
) -> dict:
    """Create a category in the current project.

    When ``color`` is omitted the palette assigns one automatically.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    try:
        return category_service.create_category(
            name=name,
            color=color or "",
            project_id=proj["id"],
        )
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool
def rename_category(
    category: str,
    new_name: str,
    color: Optional[str] = None,
    user_explicitly_approved: bool = False,
    project: Optional[str] = None,
) -> dict:
    """Rename (and optionally recolor) a category after user confirmation.

    Renaming rewrites the category of every task that uses it, so without
    ``user_explicitly_approved=True`` the tool returns ``ACTION_REQUIRED``.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    cat = _resolve_category(proj, category)
    if not cat:
        return {"error": f"Category not found in project {proj['slug']}: {category}"}
    if not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": (
                f"Переименование категории обновит все задачи. Спроси пользователя: "
                f"'Переименовать категорию «{cat['name']}» в «{new_name}»?'"
            ),
        }
    try:
        updated = category_service.update_category(
            cat["id"], name=new_name, color=color
        )
    except ValueError as e:
        return {"error": str(e)}
    return updated or {"error": f"Category {cat['id']} not found"}


@mcp.tool
def delete_category(
    category: str,
    user_explicitly_approved: bool = False,
    project: Optional[str] = None,
) -> dict:
    """Delete an unused category after explicit user confirmation.

    A category that still has tasks, or the last category of a project, is
    rejected by the service. Without approval the tool returns
    ``ACTION_REQUIRED``.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    cat = _resolve_category(proj, category)
    if not cat:
        return {"error": f"Category not found in project {proj['slug']}: {category}"}
    if not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": (
                f"Удаление категории необратимо. Спроси пользователя: "
                f"'Удалить категорию «{cat['name']}»?'"
            ),
        }
    try:
        deleted = category_service.delete_category(cat["id"])
    except ValueError as e:
        return {"error": str(e)}
    return {
        "status": "deleted" if deleted else "not_found",
        "category_id": cat["id"],
        "name": cat["name"],
    }


@mcp.tool
def get_boards(project: Optional[str] = None) -> List[dict]:
    """Return all boards of the current project with their task counts.

    The main board has ``is_default=1``. Use a board id or name as the ``board``
    argument of get_tasks/get_board/create_task to work with another board.
    """
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    return boards_service.list_boards(proj["id"])


@mcp.tool
def create_board(name: str, project: Optional[str] = None) -> dict:
    """Create an additional board in the current project."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    try:
        return boards_service.create_board(proj["id"], name)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool
def rename_board(board: str, new_name: str, project: Optional[str] = None) -> dict:
    """Rename a board of the current project (the main board can be renamed)."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
    if err:
        return {"error": err}
    try:
        return boards_service.update_board(brd["id"], name=new_name)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool
def delete_board(
    board: str,
    user_explicitly_approved: bool = False,
    project: Optional[str] = None,
) -> dict:
    """Delete an empty non-main board after explicit user confirmation.

    The service still rejects the main board and any board that contains
    tasks. Without ``user_explicitly_approved=True`` the tool returns
    ``ACTION_REQUIRED``.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
    if err:
        return {"error": err}
    if not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": (
                f"Удаление доски необратимо. Спроси пользователя: "
                f"'Удалить доску «{brd['name']}»?'"
            ),
        }
    try:
        deleted = boards_service.delete_board(brd["id"])
    except ValueError as e:
        return {"error": str(e)}
    return {
        "status": "deleted" if deleted else "not_found",
        "board_id": brd["id"],
        "name": brd["name"],
    }


@mcp.tool
def get_board(project: Optional[str] = None, board: Optional[str] = None) -> dict:
    """Return project info, the selected board, columns, categories and tasks.

    ``board`` can be an id or name; when omitted the URL ``?board=`` parameter
    is used, then the main board. The main board includes every task of the
    project (``scope="all"``); an additional board includes only its own tasks.
    Use this instead of fetching the REST API when you need the full board.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
    if err:
        return {"error": err}
    cols = task_service.list_columns(project_id=proj["id"])
    cats = category_service.list_categories(project_id=proj["id"])
    boards = boards_service.list_boards(proj["id"])
    tasks = task_service.list_tasks(
        project_id=proj["id"], board_id=_board_scope(proj, brd)
    )
    for t in tasks:
        t["last_comment"] = _last_comment_summary(t)
        for key in (
            "last_comment_at",
            "last_comment_author",
            "last_comment_text",
            "last_comment_length",
            "last_comment_kind",
        ):
            t.pop(key, None)
        t["project"] = proj["slug"]
        t["board"] = brd["name"]
    return {
        "project": {
            "id": proj["id"],
            "name": proj["name"],
            "slug": proj["slug"],
            "description": proj.get("description", ""),
        },
        "board": brd,
        "scope": "all" if brd.get("is_default") else "board",
        "boards": boards,
        "columns": cols,
        "categories": cats,
        "tasks": tasks,
    }


@mcp.tool
def get_task(
    task_id: int,
    include_comments: bool = True,
    comments_since_id: Optional[int] = None,
    comments_limit: Optional[int] = None,
    project: Optional[str] = None,
) -> dict:
    """Return one task together with its comment thread and stage navigation.

    Comments are included by default because the owner writes new requirements
    and review notes there. ``comments_since_id`` returns only comments newer
    than that id; ``comments_limit`` keeps only the last N comments.
    ``stage.allowed_moves`` lists the columns reachable by request_move_task.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    t["project"] = proj["slug"]
    t["stage"] = _stage_info(t)
    t["last_comment"] = _last_comment_summary(t)
    if include_comments:
        comments = comment_service.list_comments(
            task_id, since_id=comments_since_id, limit=comments_limit
        )
        t["comments"] = comments
    for key in (
        "last_comment_at",
        "last_comment_author",
        "last_comment_text",
        "last_comment_length",
        "last_comment_kind",
    ):
        t.pop(key, None)
    return t


@mcp.tool
def get_task_comments(
    task_id: int,
    since_id: Optional[int] = None,
    limit: Optional[int] = None,
    kind: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """Return the comment thread of one task in chronological order.

    Use ``since_id`` to fetch only comments newer than the last id you have
    seen (incremental polling); ``limit`` keeps the last N comments.
    ``kind`` filters comments: ``message`` is plain discussion,
    ``verify``/``reject`` are owner review decisions. The response also
    reports ``comment_count``, the maximum comment id and the latest review
    decision (``review_comment``).
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    comments = comment_service.list_comments(
        task_id, since_id=since_id, limit=limit, kind=kind
    )
    return {
        "task_id": task_id,
        "title": t["title"],
        "column": t.get("column_name"),
        "comment_count": t.get("comment_count") or 0,
        "last_comment_id": comments[-1]["id"] if comments else None,
        "review_comment": comment_service.get_last_review_comment(task_id),
        "comments": comments,
    }


@mcp.tool
def get_last_review_comment(task_id: int, project: Optional[str] = None) -> dict:
    """Return only the latest owner review decision for a task.

    The owner's verdict is stored as a dedicated comment with
    ``kind="verify"`` (task approved) or ``kind="reject"`` (task sent back
    with a reason). There is always at most one latest decision, so this tool
    is the cheap way to check what the owner decided without downloading the
    whole discussion thread.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    review = comment_service.get_last_review_comment(task_id)
    return {
        "task_id": task_id,
        "title": t["title"],
        "column": t.get("column_name"),
        "rejections": t.get("rejections") or 0,
        "verified_at": t.get("verified_at"),
        "review_comment": review,
    }


@mcp.tool
def get_review_queue(
    board: Optional[str] = None, project: Optional[str] = None
) -> dict:
    """Return tasks waiting for the owner's verification (review stage).

    The review stage is the third column by position (``Реализовано`` in the
    default Russian workflow). Tasks are ordered oldest activity first. Each
    task carries ``criteria_done``/``criteria_total`` and ``stalled_hours``.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
    if err:
        return {"error": err}
    cid = task_service.review_stage_id(proj["id"])
    col = task_service.get_column_by_id(cid, project_id=proj["id"]) if cid else None
    tasks = task_service.list_review_tasks(
        project_id=proj["id"], board_id=_board_scope(proj, brd)
    )
    out = []
    for t in tasks:
        item = dict(t)
        item["stalled_hours"] = _hours_since(t.get("updated_at"))
        item["last_comment"] = _last_comment_summary(t)
        for key in (
            "last_comment_at",
            "last_comment_author",
            "last_comment_text",
            "last_comment_length",
            "last_comment_kind",
        ):
            item.pop(key, None)
        out.append(item)
    return {
        "stage": col["name"] if col else None,
        "count": len(out),
        "tasks": out,
    }


@mcp.tool
def reject_task(
    agent_id: str,
    task_id: int,
    reason: str,
    return_to: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """Reject a task with a required reason and move it one stage back.

    Increments the task's rejection counter and writes the reason as a comment,
    so the next agent sees exactly what to fix. ``return_to`` overrides the
    default previous stage.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    target_id = None
    if return_to is not None:
        target_id = _col_id(return_to, proj["id"])
        if target_id is None:
            return {"error": f"Unknown column: {return_to}"}
    agent_service.touch_agent(agent_id)
    try:
        updated = task_service.reject_task(
            task_id,
            reason,
            actor=f"agent:{agent_id}",
            author=f"Agent:{agent_id}",
            return_to_column_id=target_id,
        )
    except ValueError as e:
        return {"error": str(e)}
    if not updated:
        return {"error": f"Task {task_id} not found"}
    return {
        "status": "rejected",
        "rejections": updated.get("rejections"),
        "task": updated,
    }


@mcp.tool
def verify_task(
    agent_id: str,
    task_id: int,
    note: str = "",
    move_to_next: bool = True,
    project: Optional[str] = None,
) -> dict:
    """Mark a task as verified by the owner; optionally move it forward.

    Verification is recorded in the history and as a comment. By default the
    task moves to the next stage (``Реализовано`` → ``Проверено``).
    """
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
        updated = task_service.verify_task(
            task_id,
            actor=f"agent:{agent_id}",
            note=note,
            move_to_next=move_to_next,
            author=f"Agent:{agent_id}",
        )
    except ValueError as e:
        return {"error": str(e)}
    if not updated:
        return {"error": f"Task {task_id} not found"}
    return {
        "status": "verified",
        "verified_at": updated.get("verified_at"),
        "task": updated,
    }


@mcp.tool
def set_acceptance_criteria(
    agent_id: str,
    task_id: int,
    criteria: List[str],
    project: Optional[str] = None,
) -> dict:
    """Replace the acceptance criteria of a task (list of short DoD items).

    Already checked items with the same text keep their marks. Complete them
    with check_criterion, which requires evidence.
    """
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
        updated = task_service.set_acceptance_criteria(
            task_id, criteria, actor=f"agent:{agent_id}"
        )
    except ValueError as e:
        return {"error": str(e)}
    return {
        "status": "criteria_updated",
        "criteria_done": updated.get("criteria_done"),
        "criteria_total": updated.get("criteria_total"),
        "task": updated,
    }


@mcp.tool
def check_criterion(
    agent_id: str,
    task_id: int,
    index: int,
    evidence: str,
    done: bool = True,
    project: Optional[str] = None,
) -> dict:
    """Mark acceptance criterion ``index`` (1-based) as done, with evidence.

    Evidence is mandatory when completing a criterion (build output, test
    result, manual verification). Pass ``done=false`` to reopen it.
    """
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
        updated = task_service.check_criterion(
            task_id,
            index,
            evidence=evidence,
            done=done,
            actor=f"agent:{agent_id}",
        )
    except ValueError as e:
        return {"error": str(e)}
    return {
        "status": "checked" if done else "reopened",
        "criteria_done": updated.get("criteria_done"),
        "criteria_total": updated.get("criteria_total"),
        "task": updated,
    }


@mcp.tool
def archive_task(
    agent_id: str,
    task_id: int,
    user_explicitly_approved: bool = False,
    project: Optional[str] = None,
) -> dict:
    """Archive a task (hide it from the board without deleting).

    Archiving is reversible, but it is still a user-visible action: without
    ``user_explicitly_approved=True`` the tool returns ``ACTION_REQUIRED``.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    if not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": (
                f"Архивирование скроет задачу с доски. Спроси пользователя: "
                f"'Архивировать задачу #{task_id} ({t['title']})?'"
            ),
        }
    agent_service.touch_agent(agent_id)
    updated = task_service.archive_task(task_id, actor=f"agent:{agent_id}")
    return {"status": "archived", "task": updated}


@mcp.tool
def restore_task(agent_id: str, task_id: int, project: Optional[str] = None) -> dict:
    """Restore an archived task back to the board."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    agent_service.touch_agent(agent_id)
    updated = task_service.restore_task(task_id, actor=f"agent:{agent_id}")
    return {"status": "restored", "task": updated}


@mcp.tool
def assign_task(
    agent_id: str,
    task_id: int,
    assignee: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """Assign (or unassign) the executor of a task.

    ``assignee=None`` assigns the calling agent; ``assignee=""`` clears the
    assignee. The change is recorded in the task history.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    new_assignee = agent_id if assignee is None else str(assignee).strip()
    previous = t.get("agent_id") or ""
    agent_service.touch_agent(agent_id)
    updated = task_service.update_task(
        task_id, TaskUpdate(agent_id=new_assignee), actor=f"agent:{agent_id}"
    )
    if not updated:
        return {"error": f"Task {task_id} not found"}
    if previous == new_assignee:
        status = "unchanged"
    elif new_assignee:
        status = "assigned"
    else:
        status = "unassigned"
    return {
        "status": status,
        "task_id": task_id,
        "assigned_to": updated.get("agent_id"),
        "task": updated,
    }


@mcp.tool
def get_work_queue(
    board: Optional[str] = None,
    assignee: Optional[str] = None,
    only_needs_attention: bool = False,
    project: Optional[str] = None,
) -> dict:
    """Return the active work stage queue (usually ``In Progress``).

    The stage is chosen by position (second column), so localized names work.
    Tasks are sorted by activity; ``needs_attention`` is true when the owner
    wrote the latest comment, i.e. the task is waiting for an agent reply.
    ``only_needs_attention`` keeps just those tasks.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
    if err:
        return {"error": err}
    col = task_service.active_work_column(proj["id"])
    if col is None:
        return {"stage": None, "count": 0, "needs_attention": 0, "tasks": []}
    tasks = task_service.list_tasks(
        project_id=proj["id"],
        column_id=col["id"],
        agent_id=assignee,
        board_id=_board_scope(proj, brd),
        order_by="activity",
    )
    out = []
    for t in tasks:
        item = dict(t)
        item["project"] = proj["slug"]
        item["board"] = brd["name"]
        item["stalled_hours"] = _hours_since(t.get("updated_at"))
        last = _last_comment_summary(t)
        item["last_comment"] = last
        item["needs_attention"] = bool(last and last.get("from_user"))
        for key in (
            "last_comment_at",
            "last_comment_author",
            "last_comment_text",
            "last_comment_length",
            "last_comment_kind",
        ):
            item.pop(key, None)
        if only_needs_attention and not item["needs_attention"]:
            continue
        out.append(item)
    return {
        "stage": col["name"],
        "count": len(out),
        "needs_attention": sum(1 for t in out if t["needs_attention"]),
        "tasks": out,
    }


@mcp.tool
def get_stalled_tasks(
    hours: int = 24,
    column_id: Optional[str] = None,
    priority: Optional[str] = None,
    priorities: Optional[List[str]] = None,
    board: Optional[str] = None,
    project: Optional[str] = None,
) -> List[dict]:
    """Return tasks without activity for ``hours``.

    By default only High/Critical tasks in the active stages (first two
    columns) are returned. ``column_id`` checks another stage, ``priority`` /
    ``priorities`` override the priority filter. ``stalled_hours`` shows how
    long the task has been idle.
    """
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    brd, err = _resolve_board(proj, board)
    if err:
        return [{"error": err}]
    stage_ids = None
    if column_id is not None:
        cid = _col_id(column_id, proj["id"])
        if cid is None:
            return [{"error": f"Unknown column: {column_id}"}]
        stage_ids = [cid]
    if priority is not None:
        priorities = [priority]
    elif priorities is None:
        priorities = ["High", "Critical"]
    tasks = task_service.stalled_tasks(
        hours=hours,
        project_id=proj["id"],
        board_id=_board_scope(proj, brd),
        priorities=priorities,
        stage_ids=stage_ids,
    )
    out = []
    for t in tasks:
        item = dict(t)
        item["project"] = proj["slug"]
        item["board"] = brd["name"]
        item["stalled_hours"] = _hours_since(t.get("updated_at"))
        item["last_comment"] = _last_comment_summary(t)
        for key in (
            "last_comment_at",
            "last_comment_author",
            "last_comment_text",
            "last_comment_length",
            "last_comment_kind",
        ):
            item.pop(key, None)
        out.append(item)
    return out


@mcp.tool
def get_activity(
    since: Optional[str] = None,
    board: Optional[str] = None,
    limit: int = 100,
    project: Optional[str] = None,
) -> dict:
    """Return board activity (comments and audit events) since a timestamp.

    ``since`` is ISO (e.g. ``2026-10-31T12:00:00Z``); omitted means the last
    24 hours. ``limit`` keeps the newest items. Items have ``kind`` set to
    ``event`` or ``comment`` and are returned oldest-first.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
    if err:
        return {"error": err}
    items = activity_service.get_activity(
        proj["id"], board_id=_board_scope(proj, brd), since=since, limit=limit
    )
    return {
        "project": proj["slug"],
        "board": brd["name"],
        "scope": "all" if brd.get("is_default") else "board",
        "since": since or "last 24 hours",
        "count": len(items),
        "items": items,
    }


@mcp.tool
def update_task(
    agent_id: str,
    task_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    affected_files: Optional[str] = None,
    board: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """Update task fields. Status changes must go through request_move_task.

    Category is optional, but when supplied it MUST be one of the project's
    categories (see get_categories); a foreign or unknown category is rejected.
    ``board`` (id or name) moves the task to another board of the same project.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    board_id = None
    if board is not None:
        brd, err = _resolve_board(proj, board)
        if err:
            return {"error": err}
        board_id = brd["id"]
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
                board_id=board_id,
            ),
            actor=f"agent:{agent_id}",
        )
    except ValueError as e:
        return {"error": str(e)}
    if updated:
        updated["project"] = proj["slug"]
    return updated


@mcp.tool
def move_task_to_board(
    agent_id: str,
    task_id: int,
    target_board: str,
    project: Optional[str] = None,
) -> dict:
    """Move a task to another board of the same project.

    Columns/categories are shared by boards, so the task keeps its status and
    category. Use this when the user wants to reorganize tasks between boards.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    t = task_service.get_task(task_id)
    if not t:
        return {"error": f"Task {task_id} not found"}
    if t.get("project_id") != proj["id"]:
        return {"error": f"Task {task_id} not in project {proj['slug']}"}
    brd, err = _resolve_board(proj, target_board)
    if err:
        return {"error": err}
    agent_service.touch_agent(agent_id)
    try:
        updated = task_service.update_task(
            task_id, TaskUpdate(board_id=brd["id"]), actor=f"agent:{agent_id}"
        )
    except ValueError as e:
        return {"error": str(e)}
    if updated:
        updated["project"] = proj["slug"]
    return {"status": "moved", "task": updated}


@mcp.tool
def bulk_move_tasks(
    agent_id: str,
    task_ids: List[int],
    target_column: Optional[str] = None,
    target_board: Optional[str] = None,
    user_explicitly_approved: bool = False,
    reason: str = "",
    project: Optional[str] = None,
) -> dict:
    """Move several tasks in one call.

    Pass exactly one target: ``target_column`` (id or name) or ``target_board``
    (id or name). Column moves still follow the adjacent-stage rule, so a task
    that is not next to the target column is reported in ``failed`` instead of
    being forced. Board moves keep the task's column.

    Column moves require user confirmation when ``strict_move_confirmation`` is
    enabled: without ``user_explicitly_approved=True`` the tool returns
    ``ACTION_REQUIRED``. ``reason`` is saved in the history of every moved task;
    unlike request_move_task it does not add a separate comment per task.
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    if not task_ids:
        return {"error": "task_ids must be a non-empty list"}
    if len(task_ids) > 100:
        return {"error": "At most 100 tasks per call"}
    if (target_column is None) == (target_board is None):
        return {"error": "Pass exactly one of target_column or target_board"}

    # De-duplicate while keeping the caller's order.
    seen = set()
    ordered_ids = []
    for tid in task_ids:
        if tid not in seen:
            seen.add(tid)
            ordered_ids.append(tid)

    agent_service.touch_agent(agent_id)
    actor = f"agent:{agent_id}"
    reason = (reason or "").strip()
    moved: List[dict] = []
    unchanged: List[dict] = []
    failed: List[dict] = []

    tasks: List[dict] = []
    for tid in ordered_ids:
        t = task_service.get_task(tid)
        if not t:
            failed.append({"task_id": tid, "error": f"Task {tid} not found"})
            continue
        if t.get("project_id") != proj["id"]:
            failed.append(
                {"task_id": tid, "error": f"Task {tid} not in project {proj['slug']}"}
            )
            continue
        tasks.append(t)

    if target_column is not None:
        cid = _col_id(target_column, proj["id"])
        if cid is None:
            return {"error": f"Unknown target column: {target_column}"}
        col = task_service.get_column_by_id(cid, project_id=proj["id"])
        target_name = col["name"] if col else str(target_column)

        review_id = task_service.review_stage_id(proj["id"])
        if cid == review_id and not user_explicitly_approved:
            incomplete = [
                t
                for t in tasks
                if (t.get("criteria_total") or 0)
                and t.get("criteria_done", 0) < t.get("criteria_total", 0)
            ]
            if incomplete:
                return {
                    "status": "ACTION_REQUIRED",
                    "message": (
                        f"У {len(incomplete)} задач(и) не закрыты критерии приёмки. "
                        "Закрой их через check_criterion с evidence или явно "
                        "подтверди перенос (user_explicitly_approved=true)."
                    ),
                    "task_ids": [t["id"] for t in incomplete],
                    "target_column": target_name,
                }

        strict = settings_service.is_enabled("strict_move_confirmation")
        if strict and not user_explicitly_approved and tasks:
            return {
                "status": "ACTION_REQUIRED",
                "message": (
                    f"Требуется подтверждение владельца. Задай пользователю вопрос: "
                    f"'Переместить выбранные задачи ({len(tasks)}) в колонку «{target_name}»?'"
                ),
                "task_ids": [t["id"] for t in tasks],
                "target_column": target_name,
            }

        for t in tasks:
            tid = t["id"]
            if t["column_id"] == cid:
                unchanged.append(
                    {"task_id": tid, "title": t["title"], "column": target_name}
                )
                continue
            try:
                task_service.move_task(tid, cid, actor=actor, note=reason or None)
            except ValueError as e:
                failed.append({"task_id": tid, "title": t["title"], "error": str(e)})
                continue
            moved.append(
                {
                    "task_id": tid,
                    "title": t["title"],
                    "from_column": t.get("column_name"),
                    "to_column": target_name,
                }
            )
        target = {"type": "column", "name": target_name}

    else:
        brd, err = _resolve_board(proj, target_board)
        if err:
            return {"error": err}
        for t in tasks:
            tid = t["id"]
            if t.get("board_id") == brd["id"]:
                unchanged.append(
                    {"task_id": tid, "title": t["title"], "board": brd["name"]}
                )
                continue
            try:
                task_service.update_task(
                    tid, TaskUpdate(board_id=brd["id"]), actor=actor
                )
            except ValueError as e:
                failed.append({"task_id": tid, "title": t["title"], "error": str(e)})
                continue
            moved.append(
                {
                    "task_id": tid,
                    "title": t["title"],
                    "from_board": t.get("board_name"),
                    "to_board": brd["name"],
                }
            )
        target = {"type": "board", "name": brd["name"]}

    status = "completed"
    if failed:
        status = "partial" if moved else "failed"
    return {
        "status": status,
        "target": target,
        "moved": moved,
        "unchanged": unchanged,
        "failed": failed,
        "count": len(moved),
        "unchanged_count": len(unchanged),
        "failed_count": len(failed),
    }


@mcp.tool
def create_task(
    agent_id: str,
    title: str,
    description: str,
    category: str,
    priority: str,
    affected_files: Optional[str] = "",
    column_id: Optional[str] = None,
    acceptance_criteria: Optional[List[str]] = None,
    project: Optional[str] = None,
    board: Optional[str] = None,
) -> dict:
    """Create a structured task on the selected board of the current project.

    ``category`` is required and MUST be one of the names returned by
    get_categories for this project (defaults: Feature, Bug, Rework, Balance,
    Cleanup). An unknown or foreign category is rejected.

    ``acceptance_criteria`` is an optional list of DoD items. Check them with
    check_criterion (requires evidence) before moving the task to review.

    ``board`` can be an id or name; when omitted the URL ``?board=`` parameter
    is used, then the main board. A board of another project is rejected.
    The main board is an aggregate view: if the project has additional boards,
    the task must target one of them (the call fails with the available list).
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
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
            board_id=brd["id"],
            require_board=True,
            acceptance_criteria=acceptance_criteria,
        )
    except ValueError as e:
        return {"error": str(e)}
    if t:
        t["board"] = brd["name"]
    return t


@mcp.tool
def bulk_create_tasks(
    agent_id: str,
    tasks: List[dict],
    board: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """Create several tasks at once (up to 50 per call).

    Each item: ``title`` (required), ``category`` (required), ``description``,
    ``priority`` (default ``Medium``), ``affected_files``, ``column_id``
    (optional; defaults to Backlog). Invalid items are reported in ``failed``
    and do not stop the rest of the batch (partial success is possible).
    """
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    brd, err = _resolve_board(proj, board)
    if err:
        return {"error": err}
    msg = _guard_setting("allow_agent_create_tasks")
    if msg:
        return {"error": msg}
    if not isinstance(tasks, list) or not tasks:
        return {"error": "tasks must be a non-empty list"}
    if len(tasks) > 50:
        return {"error": "At most 50 tasks per call"}
    if brd.get("is_default") and boards_service.has_additional_boards(proj["id"]):
        names = ", ".join(
            b["name"]
            for b in boards_service.list_boards(proj["id"])
            if not b.get("is_default")
        )
        return {
            "error": (
                "Choose a task board: the main board shows all project tasks and "
                f"cannot be a creation target. Available boards: {names}."
            )
        }
    agent_service.touch_agent(agent_id)

    created: List[dict] = []
    failed: List[dict] = []
    for index, raw in enumerate(tasks):
        title = None
        if isinstance(raw, dict):
            title = str(raw.get("title") or "").strip()
        try:
            if not isinstance(raw, dict):
                raise ValueError("Task must be an object")
            if not title:
                raise ValueError("title is required")
            category = raw.get("category")
            if category is None or str(category).strip() == "":
                raise ValueError("category is required (see get_categories)")
            cid = None
            if raw.get("column_id") is not None:
                cid = _col_id(raw["column_id"], proj["id"])
                if cid is None:
                    raise ValueError(f"Unknown column: {raw['column_id']}")
            task = task_service.create_task(
                title=title,
                description=str(raw.get("description") or ""),
                category=str(category),
                priority=str(raw.get("priority") or "Medium"),
                column_id=cid,
                agent_id=agent_id,
                affected_files=str(raw.get("affected_files") or ""),
                project_id=proj["id"],
                board_id=brd["id"],
                require_board=True,
                acceptance_criteria=raw.get("acceptance_criteria"),
            )
            task["board"] = brd["name"]
            created.append(task)
        except ValueError as e:
            failed.append({"index": index, "title": title, "error": str(e)})
    return {
        "created": created,
        "failed": failed,
        "count": len(created),
        "failed_count": len(failed),
    }


@mcp.tool
def analyze_and_suggest_next_task(agent_id: str, project: Optional[str] = None) -> dict:
    """Analyze tasks and suggest the next one to work on."""
    proj, err = _resolve_project(project)
    if err:
        return {"error": err}
    agent_service.touch_agent(agent_id)
    tasks = task_service.list_tasks(project_id=proj["id"])
    analysis = task_service.analyze_next(
        tasks, active_ids=task_service.active_stage_ids(proj["id"])
    )
    stalled = []
    if settings_service.is_enabled("enable_proactive_notifications"):
        stalled = task_service.stalled_tasks(
            hours=24,
            project_id=proj["id"],
            priorities=["High", "Critical"],
        )
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
    if not c:
        return {"error": "Comment was not saved"}
    updated = task_service.get_task(task_id)
    c = dict(c)
    c["task_id"] = task_id
    c["comment_count"] = updated.get("comment_count") if updated else None
    c["task_updated_at"] = updated.get("updated_at") if updated else None
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

    review_id = task_service.review_stage_id(proj["id"])
    total = t.get("criteria_total") or 0
    done = t.get("criteria_done") or 0
    if cid == review_id and total and done < total and not user_explicitly_approved:
        return {
            "status": "ACTION_REQUIRED",
            "message": (
                f"Не все критерии приёмки закрыты ({done}/{total}). "
                "Закрой их через check_criterion с evidence или явно подтверди "
                "перенос (user_explicitly_approved=true)."
            ),
            "criteria_done": done,
            "criteria_total": total,
        }

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
def get_task_history(
    task_id: int,
    include_comments: bool = True,
    project: Optional[str] = None,
) -> List[dict]:
    """Return the chronological timeline of a task in the current project.

    The timeline contains audit events (creation, moves, field updates) and,
    when ``include_comments`` is true, the task's comments, so the owner's
    feedback is visible in one place. Items have ``kind`` set to ``"event"``
    or ``"comment"``. Move notes that were auto-created together with a
    comment are not duplicated.
    """
    proj, err = _resolve_project(project)
    if err:
        return [{"error": err}]
    t = task_service.get_task(task_id)
    if not t:
        return [{"error": f"Task {task_id} not found"}]
    if t.get("project_id") != proj["id"]:
        return [{"error": f"Task {task_id} not in project {proj['slug']}"}]
    events = [dict(e, kind="event") for e in history_service.get_task_history(task_id)]
    if include_comments:
        note_texts = {
            e.get("new_value")
            for e in events
            if e.get("action") == "note" and e.get("new_value")
        }
        for c in comment_service.list_comments(task_id):
            if c.get("text") in note_texts:
                # request_move_task stores the same text as a note event.
                continue
            events.append(
                {
                    "id": c["id"],
                    "task_id": c["task_id"],
                    "action": "comment",
                    "kind": "comment",
                    "old_value": None,
                    "new_value": c["text"],
                    "actor": c.get("author") or "system",
                    "note": None,
                    "created_at": c["created_at"],
                    "old_column_id": None,
                    "new_column_id": None,
                    "old_column_name": None,
                    "new_column_name": None,
                }
            )
        events.sort(key=lambda e: (e.get("created_at") or "", e.get("kind") != "event"))
    return events
