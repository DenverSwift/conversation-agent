@echo off
setlocal
cd /d "%~dp0\.."
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is required. Install uv and run uv sync first.
  exit /b 1
)
uv run python -m conversation_agent.tools.export_training_data
