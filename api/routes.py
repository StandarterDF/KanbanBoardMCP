from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel

from models.schemas import (
    TaskCreate,
    TaskUpdate,
    CommentCreate,
    SettingUpdate,
    ProjectCreate,
    ProjectUpdate,
    ColumnCreate,
    ColumnUpdate,
    CategoryCreate,
    CategoryUpdate,
    BoardCreate,
    BoardUpdate,
    RejectRequest,
    VerifyRequest,
    CriteriaUpdate,
    CriterionCheck,
)
from services import (
    task_service,
    comment_service,
    settings_service,
    agent_service,
    project_service,
    history_service,
    category_service,
    boards_service,
    upload_service,
)

router = APIRouter(prefix="/api")


class MoveRequest(BaseModel):
    column_id: int


class ImageUploadRequest(BaseModel):
    data_url: str
    filename: Optional[str] = None


def _default_project_id(project_id: Optional[int] = None) -> int:
    if project_id is not None:
        if project_service.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project_id
    p = project_service.get_default_project()
    if not p:
        raise HTTPException(status_code=500, detail="No project configured")
    return p["id"]


# ---------- Projects ----------
@router.get("/projects")
def list_projects():
    return project_service.list_projects()


@router.post("/projects")
def create_project(payload: ProjectCreate):
    try:
        return project_service.create_project(
            name=payload.name, slug=payload.slug, description=payload.description or ""
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/projects/{project_id}")
def update_project(project_id: int, payload: ProjectUpdate):
    try:
        p = project_service.update_project(
            project_id,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


@router.delete("/projects/{project_id}")
def delete_project(project_id: int):
    # prevent deleting last project
    projects = project_service.list_projects()
    if len(projects) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last project")
    ok = project_service.delete_project(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": True}


# ---------- Boards ----------
@router.get("/boards")
def get_boards(project_id: Optional[int] = Query(None)):
    pid = _default_project_id(project_id)
    return boards_service.list_boards(project_id=pid)


@router.post("/boards")
def create_board(payload: BoardCreate):
    pid = _default_project_id(payload.project_id)
    try:
        return boards_service.create_board(pid, payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/boards/{board_id}")
def update_board(board_id: int, payload: BoardUpdate):
    try:
        board = boards_service.update_board(
            board_id, name=payload.name, order_index=payload.order_index
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    return board


@router.delete("/boards/{board_id}")
def delete_board(board_id: int):
    try:
        ok = boards_service.delete_board(board_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Board not found")
    return {"deleted": True}


# ---------- Columns ----------
@router.get("/columns")
def get_columns(project_id: Optional[int] = Query(None)):
    pid = _default_project_id(project_id)
    return task_service.list_columns(project_id=pid)


@router.post("/columns")
def create_column(payload: ColumnCreate):
    pid = _default_project_id(payload.project_id)
    col = task_service.create_column(
        name=payload.name,
        color=payload.color,
        order_index=payload.order_index,
        project_id=pid,
    )
    return col


@router.put("/columns/{column_id}")
def update_column(column_id: int, payload: ColumnUpdate):
    col = task_service.update_column(
        column_id,
        name=payload.name,
        color=payload.color,
        order_index=payload.order_index,
    )
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")
    return col


@router.delete("/columns/{column_id}")
def delete_column(column_id: int):
    try:
        ok = task_service.delete_column(column_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Column not found")
    return {"deleted": True}


# ---------- Categories ----------
@router.get("/categories")
def get_categories(project_id: Optional[int] = Query(None)):
    pid = _default_project_id(project_id)
    return category_service.list_categories(project_id=pid)


@router.post("/categories")
def create_category(payload: CategoryCreate):
    pid = _default_project_id(payload.project_id)
    try:
        return category_service.create_category(
            name=payload.name,
            color=payload.color,
            order_index=payload.order_index,
            project_id=pid,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdate):
    try:
        cat = category_service.update_category(
            category_id,
            name=payload.name,
            color=payload.color,
            order_index=payload.order_index,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


@router.delete("/categories/{category_id}")
def delete_category(category_id: int):
    try:
        ok = category_service.delete_category(category_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"deleted": True}


# ---------- Tasks ----------
@router.get("/tasks")
def get_tasks(
    project_id: Optional[int] = Query(None),
    column_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    priority: Optional[str] = None,
    priorities: Optional[List[str]] = Query(None),
    categories: Optional[List[str]] = Query(None),
    q: Optional[str] = None,
    board_id: Optional[int] = Query(None),
    include_archived: bool = False,
):
    pid = _default_project_id(project_id)
    if board_id is not None:
        board = boards_service.get_board(board_id)
        if board is None or board["project_id"] != pid:
            raise HTTPException(
                status_code=400, detail="Board does not belong to project"
            )
        # The main board is an aggregate view: it shows every task of the project.
        if board.get("is_default"):
            board_id = None
    return task_service.list_tasks(
        project_id=pid,
        column_id=column_id,
        agent_id=agent_id,
        priority=priority,
        priorities=priorities,
        categories=categories,
        q=q,
        board_id=board_id,
        include_archived=include_archived,
    )


@router.get("/tasks/{task_id}")
def get_task(task_id: int):
    t = task_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.post("/tasks")
def create_task(payload: TaskCreate):
    pid = _default_project_id(payload.project_id)
    try:
        return task_service.create_task(
            title=payload.title,
            description=payload.description,
            category=payload.category,
            priority=payload.priority,
            column_id=payload.column_id,
            agent_id=payload.agent_id,
            affected_files=payload.affected_files,
            project_id=pid,
            board_id=payload.board_id,
            require_board=True,
            acceptance_criteria=payload.acceptance_criteria,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate):
    try:
        t = task_service.update_task(task_id, payload, actor="user")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    ok = task_service.delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"deleted": True}


@router.post("/tasks/{task_id}/move")
def move_task(task_id: int, payload: MoveRequest):
    try:
        t = task_service.move_task(task_id, payload.column_id, actor="user")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.post("/tasks/{task_id}/reject")
def reject_task(task_id: int, payload: RejectRequest):
    try:
        t = task_service.reject_task(
            task_id,
            payload.reason,
            actor="user",
            author="User",
            return_to_column_id=payload.column_id,
            images=payload.images,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.post("/tasks/{task_id}/verify")
def verify_task(task_id: int, payload: VerifyRequest):
    try:
        t = task_service.verify_task(
            task_id,
            actor="user",
            note=payload.note,
            move_to_next=payload.move_to_next,
            author="User",
            images=payload.images,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.post("/tasks/{task_id}/archive")
def archive_task(task_id: int):
    t = task_service.archive_task(task_id, actor="user")
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.post("/tasks/{task_id}/restore")
def restore_task(task_id: int):
    t = task_service.restore_task(task_id, actor="user")
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.put("/tasks/{task_id}/criteria")
def set_criteria(task_id: int, payload: CriteriaUpdate):
    t = task_service.set_acceptance_criteria(task_id, payload.criteria, actor="user")
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.post("/tasks/{task_id}/criteria/{index}/check")
def check_criterion(task_id: int, index: int, payload: CriterionCheck):
    try:
        t = task_service.check_criterion(
            task_id,
            index,
            evidence=payload.evidence,
            done=payload.done,
            actor="user",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.get("/review")
def review_queue(
    project_id: Optional[int] = Query(None), board_id: Optional[int] = Query(None)
):
    pid = _default_project_id(project_id)
    return task_service.list_review_tasks(project_id=pid, board_id=board_id)


@router.get("/tasks/{task_id}/history")
def get_task_history(task_id: int, project_id: Optional[int] = Query(None)):
    t = task_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if project_id is not None and t.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="Task not found in project")
    return history_service.get_task_history(task_id)


@router.post("/uploads")
def upload_image(payload: ImageUploadRequest):
    """Save a pasted/selected image and return its public URL."""
    try:
        return upload_service.save_data_url(payload.data_url, payload.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tasks/{task_id}/comments")
def get_comments(task_id: int, kind: Optional[str] = None):
    return comment_service.list_comments(task_id, kind=kind)


@router.get("/tasks/{task_id}/review-comment")
def get_review_comment(task_id: int):
    """Return the latest owner review decision (verify/reject) for a task."""
    return comment_service.get_last_review_comment(task_id)


@router.post("/tasks/{task_id}/comments")
def add_comment(task_id: int, payload: CommentCreate):
    if not (payload.text or "").strip() and not payload.images:
        raise HTTPException(status_code=400, detail="Комментарий пуст")
    c = comment_service.add_comment(
        task_id, payload.author, payload.text or "", images=payload.images
    )
    if not c:
        raise HTTPException(status_code=404, detail="Task not found")
    return c


@router.get("/settings")
def get_settings():
    return settings_service.list_settings()


@router.put("/settings/{key}")
def update_setting(key: str, payload: SettingUpdate):
    s = settings_service.update_setting(
        key, value=payload.value, is_enabled=payload.is_enabled
    )
    if not s:
        raise HTTPException(status_code=404, detail="Setting not found")
    return s


@router.get("/stalled")
def get_stalled(hours: int = 24, project_id: Optional[int] = Query(None)):
    pid = _default_project_id(project_id)
    return task_service.stalled_tasks(
        hours=hours, project_id=pid, priorities=["High", "Critical"]
    )


@router.get("/analyze")
def analyze(project_id: Optional[int] = Query(None)):
    pid = _default_project_id(project_id)
    tasks = task_service.list_tasks(project_id=pid)
    suggestion = task_service.analyze_next(
        tasks, active_ids=task_service.active_stage_ids(pid)
    )
    stalled = []
    if settings_service.is_enabled("enable_proactive_notifications"):
        stalled = task_service.stalled_tasks(
            hours=24, project_id=pid, priorities=["High", "Critical"]
        )
    # Shape matches the MCP analyze tool and the web client.
    return {
        "suggestion": suggestion,
        "stalled_tasks": stalled,
        "total_tasks": len(tasks),
    }


@router.post("/agents/register")
def register_agent(payload: dict):
    return agent_service.register_agent(
        payload.get("agent_id"), payload.get("name"), payload.get("metadata")
    )
