@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".secrets" mkdir ".secrets"
set "PYTHONPATH=%CD%\src"
python -m conversation_agent.main login
