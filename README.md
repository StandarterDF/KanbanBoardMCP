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

```json
{
  "mcpServers": {
    "kanban-board": {
      "url": "http://127.0.0.1:34535/mcp?project=default"
    }
  }
}
```

Для другого проекта измените query-параметр `project=<slug>`. Slug проекта виден в `GET /api/projects`; у нового проекта автоматически создаются стандартные стадии (Backlog, In Progress, Implemented, Tested, Committed).

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
