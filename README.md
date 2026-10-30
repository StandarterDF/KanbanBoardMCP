# Kanban Board MCP

Локальная интерактивная Канбан-доска задач с интегрированным MCP-сервером.

## Стек
- Python 3.10+
- FastAPI + Uvicorn
- SQLite (WAL)
- FastMCP
- Vanilla JS фронтенд

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Запуск веб-интерфейса

```powershell
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 34535
```

Или двойным кликом по `start.bat` (он использует порт `34535`). Откройте http://127.0.0.1:34535

## Запуск MCP-сервера (stdio)

```powershell
.\.venv\Scripts\python.exe main.py --mcp
```

## Конфигурация MCP (Cursor / Claude Desktop / Cline)

Основной способ — HTTP Stream (веб-сервер должен быть запущен). Проект задаётся в URL:

Cursor / Claude Desktop / Cline:

```json
{
  "mcpServers": {
    "kanban-board": {
      "url": "http://127.0.0.1:34535/mcp?project=default"
    }
  }
}
```

OpenCode (V2, проект `Sekwah_Naruto_Mod`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "kanban": {
        "type": "remote",
        "url": "http://127.0.0.1:34535/mcp?project=narutomod",
        "oauth": false,
        "codemode": false
      }
    }
  }
}
```

`codemode: false` важен для OpenCode: инструменты MCP будут доступны напрямую
(`kanban_get_tasks`, `kanban_create_task`, ...), а не через `execute`/`fetch`.
Сервер сам просит агента не использовать REST API и `fetch` для операций с доской.

Для другого проекта измените query-параметр `project=<slug>`. Slug проекта виден в `GET /api/projects`; у нового проекта автоматически создаются стандартные стадии (Backlog, In Progress, Implemented, Tested, Committed) и стандартные категории: Feature, Bug, Rework, Balance, Cleanup (в этом порядке).

## Категории

Категории принадлежат проекту и настраиваются отдельно для каждого проекта.

- `GET /api/categories?project_id=...` — список категорий проекта (по `order_index`);
- `POST /api/categories` — `{ "name", "color", "project_id" }`;
- `PUT /api/categories/{id}` — `{ "name", "color", "order_index" }`;
- `DELETE /api/categories/{id}` — запрещено, если в категории есть задачи, и нельзя
  удалить последнюю категорию проекта.

Новые проекты получают набор по умолчанию: `Feature`, `Bug`, `Rework`,
`Balance`, `Cleanup`. Переименование категории обновляет задачи, которые её
используют. Задачу можно создать или изменить только с категорией из её проекта;
проверка выполняется в сервисном слое, поэтому MCP не может её обойти. В UI
категориями можно управлять в настройках (⚙ → «Категории проекта»).

MCP-инструменты: `get_categories(project=None)` возвращает категории проекта,
`get_board` включает их в ответ. Для `create_task` категория обязательна, а
`update_task` отвергает категорию, которой нет в проекте.


## Стадии, история и MCP

- Задачу можно переместить только на соседнюю стадию (предыдущую или следующую в
  порядке `order_index, id`). Ограничение действует и в UI (drag-and-drop / кнопки
  статуса), и в `POST /api/tasks/{id}/move`, `PUT /api/tasks/{id}`, и в MCP.
- `GET /api/tasks/{task_id}/history` возвращает таймлайн задачи: создание,
  перемещения, изменения полей и заметки. Названия колонок подставляются, даже
  если в `audit_log` сохранены только id (для удалённых колонок — fallback).
- MCP-инструмент `request_move_task` принимает необязательный `reason`. При
  успешном перемещении агент автоматически добавляет комментарий от
  `Agent:<agent_id>`, а причина сохраняется в истории.
- MCP-инструмент `get_task_history(task_id, project=None)` возвращает те же
  события истории.
- MCP-инструменты `get_board`, `get_categories`, `get_columns`, `get_task`,
  `update_task` позволяют агенту работать с доской без HTTP-запросов к REST API.
- `delete_task(task_id, user_explicitly_approved=True)` удаляет задачу только
  после явного подтверждения пользователя; без флага возвращается
  `ACTION_REQUIRED`.

Fallback (stdio, проект по умолчанию или env `KANBAN_PROJECT`):

```json
{
  "mcpServers": {
    "kanban-board-stdio": {
      "command": "E:/DefaultDF/Programming/Qwen/Projects/KabanBoardMCP/.venv/Scripts/python.exe",
      "args": ["E:/DefaultDF/Programming/Qwen/Projects/KabanBoardMCP/main.py", "--mcp"]
    }
  }
}
```

## Структура
- `main.py` — точка входа
- `db/` — SQLite слой
- `services/` — бизнес-логика
- `api/` — FastAPI роуты
- `mcp_server/` — MCP инструменты
- `static/` — фронтенд
- `utils/git_init.py` — автоинициализация git
