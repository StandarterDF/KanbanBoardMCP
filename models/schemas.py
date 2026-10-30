from typing import Optional, List
from pydantic import BaseModel, Field


class Column(BaseModel):
    id: int
    name: str
    order_index: int
    color: str


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    category: str = "Feature"
    priority: str = "Medium"
    column_id: Optional[int] = None
    agent_id: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    column_id: Optional[int] = None


class Task(BaseModel):
    id: int
    title: str
    description: str
    category: str
    priority: str
    column_id: int
    column_name: Optional[str] = None
    agent_id: Optional[str] = None
    created_at: str
    updated_at: str
    comment_count: int = 0


class CommentCreate(BaseModel):
    author: str = "User"
    text: str


class Comment(BaseModel):
    id: int
    task_id: int
    author: str
    text: str
    created_at: str


class AgentRegister(BaseModel):
    agent_id: str
    name: str
    metadata: Optional[dict] = None


class SettingUpdate(BaseModel):
    value: Optional[str] = None
    is_enabled: Optional[bool] = None


class Setting(BaseModel):
    key: str
    value: str
    description: str
    is_enabled: bool


VALID_PRIORITIES = ["Low", "Medium", "High", "Critical"]
VALID_CATEGORIES = ["Feature", "Bug", "Rework", "Balance", "Cleanup"]
