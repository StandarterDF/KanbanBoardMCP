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
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 8000
```

Откройте http://127.0.0.1:8000

## Запуск MCP-сервера (stdio)

```powershell
.\.venv\Scripts\python.exe main.py --mcp
```

## Конфигурация MCP (Cursor / Claude Desktop / Cline)

```json
{
  "mcpServers": {
    "kanban-board": {
      "command": "E:/DefaultDF/Programming/Qwen/Projects/KabanBoardMCP/.venv/Scripts/python.exe",
      "args": ["E:/DefaultDF/Programming/Qwen/Projects/KabanBoardMCP/main.py", "--mcp"]
    }
  }
}
```

Также доступен HTTP (streamable) эндпоинт: `http://127.0.0.1:8000/mcp` при запущенном веб-сервере.

## Структура
- `main.py` — точка входа
- `db/` — SQLite слой
- `services/` — бизнес-логика
- `api/` — FastAPI роуты
- `mcp_server/` — MCP инструменты
- `static/` — фронтенд
- `utils/git_init.py` — автоинициализация git
