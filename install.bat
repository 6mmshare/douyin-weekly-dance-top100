@echo off
setlocal EnableExtensions
title Douyin Weekly Dance Top100 - Setup

cd /d "%~dp0"
if errorlevel 1 goto :path_error

if not exist "logs" mkdir "logs" >nul 2>&1

echo ==============================================
echo Douyin Weekly Dance Top100 - Installer
echo ==============================================
echo.
echo [1/4] Checking Python 3.10 or newer...

where py >nul 2>&1
if not errorlevel 1 goto :use_py

where python >nul 2>&1
if not errorlevel 1 goto :use_python

echo ERROR: Python 3 was not found.
echo Install Python 3.10 or newer, then run this file again.
goto :fail

:use_py
set "PY_CMD=py -3"
goto :python_ready

:use_python
set "PY_CMD=python"
goto :python_ready

:python_ready
%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.10 or newer is required.
  goto :fail
)

if exist ".venv\Scripts\python.exe" (
  echo [2/4] Existing virtual environment found.
) else (
  echo [2/4] Creating virtual environment...
  %PY_CMD% -m venv ".venv"
  if errorlevel 1 goto :fail
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo ERROR: Virtual environment was not created correctly.
  goto :fail
)

echo [3/4] Installing Python packages...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"%VENV_PY%" -m pip install -r "%CD%\requirements.txt"
if errorlevel 1 goto :fail

echo [4/4] Installing Playwright Chromium...
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 goto :fail

echo.
echo ==============================================
echo INSTALL COMPLETED
echo Next: double-click login_once.bat
echo ==============================================
pause
exit /b 0

:path_error
echo ERROR: Cannot enter the folder containing this BAT file.
echo Move the extracted folder to a simple path such as:
echo D:\weekly_dance_douyin_top100
goto :fail

:fail
echo.
echo INSTALL FAILED.
echo Keep this window open and send a full screenshot of the error.
pause
exit /b 1
