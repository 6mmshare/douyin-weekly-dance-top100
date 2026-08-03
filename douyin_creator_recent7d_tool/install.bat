@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Check Shared Douyin Runtime

call "%~dp0_使用上一级环境.bat"
if errorlevel 1 goto fail

set "SHARED_PROFILE=%~dp0..\data\profiles\douyin"

echo Checking shared runtime...
echo This script will not install or download anything.
echo.
echo Python:
echo %PYTHON%
echo.
echo Shared Douyin profile:
echo %SHARED_PROFILE%
echo.

"%PYTHON%" -c "import sys, yaml, openpyxl, playwright; print('Python modules: OK'); print('Executable:', sys.executable)"
if errorlevel 1 goto fail

"%PYTHON%" -c "from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); exe=Path(p.chromium.executable_path); print('Chromium:', exe); ok=exe.exists(); p.stop(); raise SystemExit(0 if ok else 2)"
if errorlevel 1 goto fail

if exist "%SHARED_PROFILE%" (
    echo Shared login profile: FOUND
) else (
    echo Shared login profile: NOT FOUND
    echo It will be created automatically when login_once.bat is run.
)

echo.
echo Environment check passed.
echo No new virtual environment was created.
echo No dependency was installed.
echo No browser was downloaded.
echo.
echo If the original Top100 browser is already logged in,
echo this tool will reuse the same login state.
pause
exit /b 0

:fail
echo.
echo [ERROR] Shared environment check failed.
echo Please take a screenshot of this window.
pause
exit /b 1
