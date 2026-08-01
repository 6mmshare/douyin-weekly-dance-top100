@echo off
setlocal EnableExtensions
title Douyin Top100 - Resumable Visible Run

cd /d "%~dp0"
if errorlevel 1 goto :path_error
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo ERROR: Run install.bat first.
  pause
  exit /b 1
)

echo ==============================================
echo Visible resumable recovery mode.
echo Use this only when background mode reports
echo verification, login expiry, or empty data.
echo ==============================================
echo.

"%VENV_PY%" "%CD%\weekly_dance_ranker.py" run --visible
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo VISIBLE RUN FAILED. Exit code: %RC%
  echo Check logs\weekly_ranker.log and debug_screenshots.
  pause
  exit /b %RC%
)

echo.
echo VISIBLE RUN COMPLETED.
pause
exit /b 0

:path_error
echo ERROR: Cannot enter the tool folder.
pause
exit /b 1
