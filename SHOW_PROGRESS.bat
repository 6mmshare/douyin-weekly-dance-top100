@echo off
setlocal EnableExtensions
title Douyin Top100 - Progress
cd /d "%~dp0"
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (echo ERROR: Run install.bat first.& pause& exit /b 1)
"%VENV_PY%" "%CD%\weekly_dance_ranker.py" progress
echo.
pause
