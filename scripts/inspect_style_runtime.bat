@echo off
setlocal
cd /d "%~dp0.."
uv run python -m conversation_agent.tools.inspect_style_runtime %*
exit /b %ERRORLEVEL%
