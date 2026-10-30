from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel

from models.schemas import TaskCreate, TaskUpdate, CommentCreate, SettingUpdate
from services import task_service, comment_service, settings_service, agent_service

router = APIRouter(prefix="/api")


class MoveRequest(BaseModel):
    column_id: int


@router.get("/columns")
def get_columns():
    return task_service.list_columns()


@router.get("/tasks")
def get_tasks(
    column_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    priority: Optional[str] = None,
):
    return task_service.list_tasks(
        column_id=column_id, agent_id=agent_id, priority=priority
    )


@router.get("/tasks/{task_id}")
def get_task(task_id: int):
    t = task_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.post("/tasks")
def create_task(payload: TaskCreate):
    col_id = payload.column_id
    return task_service.create_task(
        title=payload.title,
        description=payload.description,
        category=payload.category,
        priority=payload.priority,
        column_id=col_id,
        agent_id=payload.agent_id,
    )


@router.put("/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate):
    t = task_service.update_task(task_id, payload, actor="user")
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
    t = task_service.move_task(task_id, payload.column_id, actor="user")
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


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
def get_stalled(hours: int = 24):
    return task_service.stalled_tasks(hours=hours)


@router.get("/analyze")
def analyze():
    tasks = task_service.list_tasks()
    return task_service.analyze_next(tasks)


@router.post("/agents/register")
def register_agent(payload: dict):
    return agent_service.register_agent(
        payload.get("agent_id"), payload.get("name"), payload.get("metadata")
    )
