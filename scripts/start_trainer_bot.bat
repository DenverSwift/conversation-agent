@echo off
setlocal
cd /d "%~dp0.."

if not exist ".env" (
  echo Missing .env. Configure trainer bot settings first.
  exit /b 1
)

uv run python -m conversation_agent.trainer.bot run
exit /b %ERRORLEVEL%
