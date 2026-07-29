@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_llama_cpp.ps1"
exit /b %errorlevel%
