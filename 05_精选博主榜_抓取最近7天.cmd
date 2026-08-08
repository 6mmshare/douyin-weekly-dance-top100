@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "TOOL=%~dp0douyin_creator_recent7d_tool"
if not exist "%PY%" (echo [ERROR] .venv Python not found.&pause&exit /b 1)
"%PY%" "%TOOL%\01_collect_recent_videos.py" collect --visible
pause
