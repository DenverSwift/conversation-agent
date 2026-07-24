@echo off
setlocal
cd /d "%~dp0.."

if not exist ".env" (
  echo Missing .env. Configure OpenAI and style settings first.
  exit /b 1
)

uv run python -m conversation_agent.tools.build_style_bundle
exit /b %ERRORLEVEL%
