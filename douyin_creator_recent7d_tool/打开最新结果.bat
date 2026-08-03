@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "data\latest_collection.txt" (echo 还没有结果，请先运行功能1&pause&exit /b 1)
set /p JSONPATH=<"data\latest_collection.txt"
for %%I in ("%JSONPATH%") do set "RESULTDIR=%%~dpI"
if exist "%RESULTDIR%02_最近7天视频Top100.html" (start "" "%RESULTDIR%02_最近7天视频Top100.html") else (explorer "%RESULTDIR%")
