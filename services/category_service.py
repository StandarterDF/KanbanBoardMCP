"""Project-scoped task categories.

Categories belong to a project. A task may only use a category that exists in
its own project; renaming a category updates the tasks that use it and deleting
a category is blocked while tasks still reference it.
"""

from typing import Any, Dict, List, Optional

from db.database import _category_color, db_cursor, now, row_to_dict
from models.schemas import normalize_category
from services import project_service


def list_categories(project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    pid = _project_id(project_id)
    with db_cursor() as (cur, conn):
        cur.execute(
            "SELECT * FROM categories WHERE project_id = ? ORDER BY order_index, id",
            (pid,),
        )
        return [row_to_dict(r) for r in cur.fetchall()]


def get_category(category_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
        r = cur.fetchone()
        return row_to_dict(r) if r else None


def _project_id(project_id: Optional[int] = None) -> int:
    if project_id is not None:
        return project_id
    p = project_service.get_default_project()
    return p["id"] if p else 1


def _same_name(a: str, b: str) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def _find_by_name(project_id: int, name: str) -> Optional[Dict[str, Any]]:
    for cat in list_categories(project_id):
        if _same_name(cat["name"], name):
            return cat
    return None


def available_names(project_id: Optional[int] = None) -> List[str]:
    return [c["name"] for c in list_categories(project_id)]


def resolve_category(project_id: Optional[int], name: Optional[str]) -> str:
    """Return the canonical category name for a project.

    Known default names are normalized case-insensitively; custom project
    categories are matched case-insensitively too. Raises ``ValueError`` with
    the available categories when the value is not part of the project.
    """
    pid = _project_id(project_id)
    cats = list_categories(pid)
    if not cats:
        raise ValueError("Project has no categories configured")
    if name is None or str(name).strip() == "":
        return cats[0]["name"]
    normalized = normalize_category(str(name).strip()).strip()
    for cat in cats:
        if _same_name(cat["name"], normalized):
            return cat["name"]
    proj = project_service.get_project(pid)
    label = proj["name"] if proj else f"#{pid}"
    available = ", ".join(c["name"] for c in cats)
    raise ValueError(
        f"Category '{name}' is not available in project '{label}'. "
        f"Available categories: {available}."
    )


def create_category(
    name: str,
    color: str = "#5b8cff",
    project_id: Optional[int] = None,
    order_index: Optional[int] = None,
) -> Dict[str, Any]:
    pid = _project_id(project_id)
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Category name is required")
    existing = _find_by_name(pid, clean)
    if existing:
        raise ValueError(f"Category '{clean}' already exists in this project")
    cats = list_categories(pid)
    if order_index is None:
        order_index = max((c["order_index"] for c in cats), default=-1) + 1
    if not color:
        color = _category_color(len(cats))
    with db_cursor() as (cur, conn):
        cur.execute(
            """INSERT INTO categories (project_id, name, color, order_index, created_at)
               VALUES (?,?,?,?,?)""",
            (pid, clean, color, order_index, now()),
        )
        cid = cur.lastrowid
    return get_category(cid)


def update_category(
    category_id: int,
    name: Optional[str] = None,
    color: Optional[str] = None,
    order_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    cat = get_category(category_id)
    if not cat:
        return None
    fields: Dict[str, Any] = {}
    old_name = cat["name"]
    new_name = old_name
    if name is not None:
        new_name = name.strip()
        if not new_name:
            raise ValueError("Category name is required")
        if not _same_name(new_name, old_name):
            duplicate = _find_by_name(cat["project_id"], new_name)
            if duplicate and duplicate["id"] != category_id:
                raise ValueError(
                    f"Category '{new_name}' already exists in this project"
                )
        fields["name"] = new_name
    if color is not None:
        fields["color"] = color
    if order_index is not None:
        fields["order_index"] = order_index
    if not fields:
        return cat
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [category_id]
    with db_cursor() as (cur, conn):
        cur.execute(f"UPDATE categories SET {sets} WHERE id = ?", params)
        # Keep tasks valid when a category is renamed (including case-only).
        if "name" in fields and old_name != new_name:
            cur.execute(
                """UPDATE tasks SET category = ?
                   WHERE project_id = ? AND category = ? COLLATE NOCASE""",
                (new_name, cat["project_id"], old_name),
            )
    return get_category(category_id)


def category_has_tasks(category: Dict[str, Any]) -> int:
    """Return the number of tasks using this category (case-insensitive)."""
    with db_cursor() as (cur, conn):
        cur.execute(
            """SELECT COUNT(*) AS c FROM tasks
               WHERE project_id = ? AND category = ? COLLATE NOCASE""",
            (category["project_id"], category["name"]),
        )
        return cur.fetchone()["c"]


def delete_category(category_id: int) -> bool:
    cat = get_category(category_id)
    if not cat:
        return False
    used = category_has_tasks(cat)
    if used:
        raise ValueError(
            f"Cannot delete category '{cat['name']}': {used} task(s) use it"
        )
    cats = list_categories(cat["project_id"])
    if len(cats) <= 1:
        raise ValueError("Cannot delete the last category of a project")
    with db_cursor() as (cur, conn):
        cur.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        return cur.rowcount > 0
