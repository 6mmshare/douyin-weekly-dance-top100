@echo off
setlocal EnableExtensions
title Douyin Top100 - Fix Timezone

cd /d "%~dp0"
if errorlevel 1 goto :path_error
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo ERROR: Virtual environment not found.
  echo Run install.bat first.
  pause
  exit /b 1
)

echo Installing Windows timezone data...
"%VENV_PY%" -m pip install --upgrade tzdata
if errorlevel 1 goto :fail

"%VENV_PY%" -c "from zoneinfo import ZoneInfo; print('Timezone OK:', ZoneInfo('Asia/Shanghai'))"
if errorlevel 1 goto :fail

echo.
echo TIMEZONE FIX COMPLETED.
echo Now double-click run_weekly.bat.
pause
exit /b 0

:path_error
echo ERROR: Cannot enter this folder.
goto :fail

:fail
echo.
echo TIMEZONE FIX FAILED.
echo Send a full screenshot of this window.
pause
exit /b 1
