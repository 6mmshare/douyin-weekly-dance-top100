@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (echo [ERROR] .venv Python not found.&pause&exit /b 1)
"%PY%" "%~dp0weekly_dance_ranker.py" login --platform douyin
pause
