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
)
from services import (
    task_service,
    comment_service,
    settings_service,
    agent_service,
    project_service,
    history_service,
)

router = APIRouter(prefix="/api")


class MoveRequest(BaseModel):
    column_id: int


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
):
    pid = _default_project_id(project_id)
    return task_service.list_tasks(
        project_id=pid,
        column_id=column_id,
        agent_id=agent_id,
        priority=priority,
        priorities=priorities,
        categories=categories,
        q=q,
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


@router.get("/tasks/{task_id}/history")
def get_task_history(task_id: int, project_id: Optional[int] = Query(None)):
    t = task_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if project_id is not None and t.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="Task not found in project")
    return history_service.get_task_history(task_id)


@router.get("/tasks/{task_id}/comments")
def get_comments(task_id: int):
    return comment_service.list_comments(task_id)


@router.post("/tasks/{task_id}/comments")
def add_comment(task_id: int, payload: CommentCreate):
    c = comment_service.add_comment(task_id, payload.author, payload.text)
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
    return task_service.stalled_tasks(hours=hours, project_id=pid)


@router.get("/analyze")
def analyze(project_id: Optional[int] = Query(None)):
    pid = _default_project_id(project_id)
    tasks = task_service.list_tasks(project_id=pid)
    return task_service.analyze_next(tasks)


@router.post("/agents/register")
def register_agent(payload: dict):
    return agent_service.register_agent(
        payload.get("agent_id"), payload.get("name"), payload.get("metadata")
    )
