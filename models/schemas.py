from typing import Optional, List
from pydantic import BaseModel, Field


class Project(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = ""
    created_at: str


class ProjectCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None


class Column(BaseModel):
    id: int
    project_id: Optional[int] = None
    name: str
    order_index: int
    color: str


class ColumnCreate(BaseModel):
    name: str
    color: str = "#4a90d9"
    order_index: Optional[int] = None
    project_id: Optional[int] = None


class ColumnUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    order_index: Optional[int] = None


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    category: str = "Feature"
    priority: str = "Medium"
    column_id: Optional[int] = None
    agent_id: Optional[str] = None
    affected_files: str = ""
    project_id: Optional[int] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    column_id: Optional[int] = None
    agent_id: Optional[str] = None
    affected_files: Optional[str] = None


class Task(BaseModel):
    id: int
    project_id: Optional[int] = None
    title: str
    description: str
    category: str
    priority: str
    column_id: int
    column_name: Optional[str] = None
    agent_id: Optional[str] = None
    affected_files: Optional[str] = ""
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
