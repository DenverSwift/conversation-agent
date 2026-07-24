@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".runtime" mkdir ".runtime"
if not exist "logs" mkdir "logs"
set "PYTHONPATH=%CD%\src"
python -m conversation_agent.main run
