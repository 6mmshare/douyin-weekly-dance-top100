@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "TOOL=%~dp0douyin_pet_dance_top100"
if not exist "%PY%" (echo [ERROR] .venv Python not found.&pause&exit /b 1)
if not exist "%TOOL%\pet_weekly_ranker.py" (echo [ERROR] pet_weekly_ranker.py not found.&pause&exit /b 2)
"%PY%" "%TOOL%\pet_weekly_ranker.py" generate
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (echo [ERROR] Pet Top100 generation failed.&pause&exit /b %ERR%)
call "%~dp010_宠物榜_打开最新结果.cmd"
