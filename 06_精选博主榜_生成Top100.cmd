@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "TOOL=%~dp0douyin_creator_recent7d_tool"
if not exist "%PY%" (echo [ERROR] .venv Python not found.&pause&exit /b 1)
if not exist "%TOOL%\02_rank_recent_videos.py" (echo [ERROR] 02_rank_recent_videos.py not found.&pause&exit /b 2)
"%PY%" "%TOOL%\02_rank_recent_videos.py"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (echo [ERROR] Creator Top100 generation failed.&pause&exit /b %ERR%)
call "%~dp007_精选博主榜_打开最新结果.cmd"
