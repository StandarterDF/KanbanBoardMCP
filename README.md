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

## Доски

В одном проекте может быть несколько досок. Колонки и категории — общие для
проекта, доска только фильтрует задачи (компоновка одинаковая).

- у каждого проекта автоматически есть главная доска «Главная» (`is_default=1`),
  её нельзя удалить;
- новые проекты получают главную доску автоматически;
- все задачи всегда привязаны к доске своего проекта.

API:

- `GET /api/boards?project_id=...` — доски проекта с количеством задач;
- `POST /api/boards` — `{ "name", "project_id" }`; имя уникально в проекте без
  учёта регистра;
- `PUT /api/boards/{id}` — `{ "name", "order_index" }` (переименование);
- `DELETE /api/boards/{id}` — запрещено для главной доски и для доски с
  задачами;
- `GET /api/tasks?project_id=...&board_id=...` — задачи одной доски;
- `POST /api/tasks` принимает `board_id` (по умолчанию — главная доска);
- `PUT /api/tasks/{id}` принимает `board_id`, но перенос на доску чужого
  проекта отклоняется.

В UI под шапкой расположены чипы досок: «Главная» и дополнительные доски.
Кнопка «+ Доска» создаёт доску, у дополнительных есть переименование (✎) и
удаление (🗑). Активная доска запоминается в `localStorage` отдельно для каждого
проекта (`activeBoardId:<projectId>`). При переключении доски перезагружаются
только задачи, а колонки, категории и фильтры остаются проекта.

MCP: `get_boards(project=None)` возвращает доски проекта. Инструменты
`get_board`, `get_tasks` и `create_task` принимают необязательный `board` (id или
имя); также можно выбрать доску в URL: `?project=<slug>&board=<id или имя>`.
По умолчанию используется главная доска; задачи других досок в выборку не
попадают.

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
