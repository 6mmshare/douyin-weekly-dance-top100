@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_使用上一级环境.bat"
if errorlevel 1 (pause&exit /b 1)
title Douyin Pet Dance Top100 Background
"%PYTHON%" pet_weekly_ranker.py run --background
pause
