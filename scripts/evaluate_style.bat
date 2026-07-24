@echo off
setlocal
cd /d "%~dp0.."
uv run python -m conversation_agent.tools.evaluate_style %*
exit /b %ERRORLEVEL%
