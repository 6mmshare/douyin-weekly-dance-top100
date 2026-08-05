@echo off
setlocal
cd /d "%~dp0"
title Generate Recent 7 Days Pet Top100

set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
set "SCRIPT=%~dp0pet_weekly_ranker.py"

if not exist "%PYTHON%" (
    echo [ERROR] Shared Python environment not found:
    echo %PYTHON%
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] Script not found:
    echo %SCRIPT%
    pause
    exit /b 1
)

echo ================================================
echo Use existing data only. No new Douyin search.
echo Existing checkpoints will not be deleted.
echo ================================================
echo.

"%PYTHON%" "%SCRIPT%" generate
set "ERR=%ERRORLEVEL%"

echo.
if "%ERR%"=="0" (
    echo [OK] Top100 generation completed.
) else (
    echo [ERROR] Generation failed. Check logs\weekly_ranker.log
)

pause
exit /b %ERR%
