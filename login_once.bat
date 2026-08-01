@echo off
setlocal EnableExtensions
title Douyin Weekly Dance Top100 - Login

cd /d "%~dp0"
if errorlevel 1 goto :path_error

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo ERROR: The tool is not installed yet.
  echo Run install.bat first and wait for INSTALL COMPLETED.
  pause
  exit /b 1
)

echo ==============================================
echo Login setup: Douyin only
echo A separate browser profile will be opened.
echo Log in once; future weekly runs will reuse it.
echo ==============================================
echo.

"%VENV_PY%" "%CD%\weekly_dance_ranker.py" login --platform douyin
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo LOGIN COMMAND FAILED. Exit code: %RC%
  echo Check: logs\weekly_ranker.log
)

echo.
pause
exit /b %RC%

:path_error
echo ERROR: Cannot enter the tool folder.
pause
exit /b 1
