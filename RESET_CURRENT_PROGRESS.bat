@echo off
setlocal EnableExtensions
title Douyin Top100 - Reset Current Progress
cd /d "%~dp0"
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (echo ERROR: Run install.bat first.& pause& exit /b 1)
echo WARNING: This deletes ONLY the current week's collection checkpoint.
echo It does NOT delete your Douyin login profile.
choice /C YN /N /M "Reset current-week progress and collect from the beginning? [Y/N] "
if errorlevel 2 exit /b 0
"%VENV_PY%" "%CD%\weekly_dance_ranker.py" reset-progress --yes
echo.
pause
