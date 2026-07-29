@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_qwen3_06b.ps1"
exit /b %errorlevel%
