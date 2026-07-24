@echo off
setlocal
cd /d "%~dp0.."

if not exist ".runtime" mkdir ".runtime"
type nul > ".runtime\trainer_bot.stop"
echo Trainer bot stop requested.
