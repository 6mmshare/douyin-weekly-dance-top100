@echo off
setlocal EnableExtensions
title Douyin Top100 - Resumable Background Compatible Run

cd /d "%~dp0"
if errorlevel 1 goto :path_error
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo ERROR: Run install.bat first.
  pause
  exit /b 1
)

echo ==============================================
echo Douyin Top100 is running in compatible background mode.
echo A normal browser engine runs outside the visible desktop.
echo No browser window should appear on screen.
echo Progress is checkpointed and can resume after interruption.
echo ==============================================
echo.

"%VENV_PY%" "%CD%\weekly_dance_ranker.py" run --background
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo WEEKLY RUN STOPPED. Exit code: %RC%
  echo Check logs\weekly_ranker.log and debug_screenshots.
  echo If verification or empty data was detected, run run_visible.bat.
  pause
  exit /b %RC%
)

echo.
echo WEEKLY RUN COMPLETED.
echo Results are under the output folder.
echo Double-click OPEN_LATEST_RESULT.bat when you want to view them.
pause
exit /b 0

:path_error
echo ERROR: Cannot enter the tool folder.
pause
exit /b 1
