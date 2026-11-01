@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo Starting Kanban Board web server on http://127.0.0.1:34535
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 34535
pause