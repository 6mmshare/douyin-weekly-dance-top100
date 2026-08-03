@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Douyin Shared Login Check

call "%~dp0_使用上一级环境.bat"
if errorlevel 1 (pause&exit /b 1)

echo This tool uses the same Douyin browser profile as the parent Top100 project:
echo %~dp0..\data\profiles\douyin
echo.
echo Close the original Top100 browser before continuing.
echo.
"%PYTHON%" 01_collect_recent_videos.py login
set "EXITCODE=%ERRORLEVEL%"
pause
exit /b %EXITCODE%
