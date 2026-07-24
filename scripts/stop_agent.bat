@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".runtime\agent.pid" (
  echo No running agent PID file found.
  exit /b 0
)
echo stop>".runtime\agent.stop"
echo Stop requested. The agent will disconnect Telegram and exit.
