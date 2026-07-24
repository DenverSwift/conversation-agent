@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".runtime" mkdir ".runtime"
if not exist "logs" mkdir "logs"
set "PYTHONPATH=%CD%\src"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m conversation_agent.main run
) else (
    python -m conversation_agent.main run
)
