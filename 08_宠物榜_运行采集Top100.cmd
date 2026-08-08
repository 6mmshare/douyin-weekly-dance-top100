@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "TOOL=%~dp0douyin_pet_dance_top100"
if not exist "%PY%" (echo [ERROR] .venv Python not found.&pause&exit /b 1)
"%PY%" "%TOOL%\pet_weekly_ranker.py" run --visible
pause
