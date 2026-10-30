import re
from typing import List, Dict, Any, Optional
from db.database import (
    db_cursor,
    now,
    row_to_dict,
    create_default_columns,
    create_default_categories,
    create_default_board,
)


_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def _slugify(name: str) -> str:
    s = (name or "").strip().lower()
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if not s:
        s = "project"
    return s


def list_projects() -> List[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM projects ORDER BY id")
        return [row_to_dict(r) for r in cur.fetchall()]


def get_project(project_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def get_project_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM projects WHERE slug = ?", (slug,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def get_default_project() -> Optional[Dict[str, Any]]:
    p = get_project_by_slug("default")
    if p:
        return p
    projects = list_projects()
    return projects[0] if projects else None


def create_project(
    name: str, slug: Optional[str] = None, description: str = ""
) -> Dict[str, Any]:
    base = slug or _slugify(name)
    # ensure uniqueness
    candidate = base
    i = 2
    while get_project_by_slug(candidate):
        candidate = f"{base}-{i}"
        i += 1
    ts = now()
    with db_cursor() as (cur, conn):
        cur.execute(
            "INSERT INTO projects (name, slug, description, created_at) VALUES (?,?,?,?)",
            (name, candidate, description or "", ts),
        )
        pid = cur.lastrowid
        create_default_columns(cur, pid)
        create_default_categories(cur, pid)
        create_default_board(cur, pid)
    return get_project(pid)


def update_project(
    project_id: int,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    p = get_project(project_id)
    if not p:
        return None
    fields = {}
    if name is not None:
        fields["name"] = name
    if description is not None:
        fields["description"] = description
    if slug is not None and slug != p["slug"]:
        # uniqueness
        existing = get_project_by_slug(slug)
        if existing and existing["id"] != project_id:
            raise ValueError("Slug already exists")
        fields["slug"] = slug
    if not fields:
        return p
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [project_id]
    with db_cursor() as (cur, conn):
        cur.execute(f"UPDATE projects SET {sets} WHERE id = ?", params)
    return get_project(project_id)


def delete_project(project_id: int) -> bool:
    p = get_project(project_id)
    if not p:
        return False
    # prevent deleting last project? allow but maybe keep default. We'll allow cascade.
    with db_cursor() as (cur, conn):
        # delete comments for tasks in project
        cur.execute(
            "DELETE FROM comments WHERE task_id IN (SELECT id FROM tasks WHERE project_id = ?)",
            (project_id,),
        )
        cur.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        cur.execute("DELETE FROM boards WHERE project_id = ?", (project_id,))
        cur.execute("DELETE FROM columns WHERE project_id = ?", (project_id,))
        cur.execute("DELETE FROM categories WHERE project_id = ?", (project_id,))
        cur.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0
