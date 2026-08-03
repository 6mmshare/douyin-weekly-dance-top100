@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 抖音博主工具 - 检查名单
call "%~dp0_使用上一级环境.bat"
if errorlevel 1 (pause&exit /b 1)
"%PYTHON%" 01_collect_recent_videos.py validate-input
set "EXITCODE=%ERRORLEVEL%"
pause
exit /b %EXITCODE%
