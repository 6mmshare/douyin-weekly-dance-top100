@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 抖音博主最近7天视频 - 生成Top100
call "%~dp0_使用上一级环境.bat"
if errorlevel 1 (pause&exit /b 1)
"%PYTHON%" 02_rank_recent_videos.py
set "EXITCODE=%ERRORLEVEL%"
pause
exit /b %EXITCODE%
