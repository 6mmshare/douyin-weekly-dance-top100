@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 抖音博主最近7天视频 - 清除断点
call "%~dp0_使用上一级环境.bat"
if errorlevel 1 (pause&exit /b 1)
"%PYTHON%" 01_collect_recent_videos.py reset --yes
set "EXITCODE=%ERRORLEVEL%"
pause
exit /b %EXITCODE%
