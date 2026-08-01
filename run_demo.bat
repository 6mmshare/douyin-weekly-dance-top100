@echo off
setlocal EnableExtensions
title Douyin Weekly Dance Top100 - Demo

cd /d "%~dp0"
if errorlevel 1 goto :path_error
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo ERROR: Run install.bat first.
  pause
  exit /b 1
)

echo Checking timezone data...
"%VENV_PY%" -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai')" >nul 2>&1
if errorlevel 1 (
  echo Timezone data is missing. Installing tzdata...
  "%VENV_PY%" -m pip install tzdata
  if errorlevel 1 (
    echo WARNING: Could not install tzdata.
    echo Continuing with the built-in Asia/Shanghai UTC+8 fallback.
  )
)

"%VENV_PY%" "%CD%\weekly_dance_ranker.py" demo
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo DEMO FAILED. Exit code: %RC%
  echo Check: logs\weekly_ranker.log
)
pause
exit /b %RC%

:path_error
echo ERROR: Cannot enter the tool folder.
pause
exit /b 1
