@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".secrets" mkdir ".secrets"
set "PYTHONPATH=%CD%\src"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m conversation_agent.main login
) else (
    python -m conversation_agent.main login
)
