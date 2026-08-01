@echo off
setlocal EnableExtensions
title Open Latest Douyin Top100 Result

cd /d "%~dp0"
if errorlevel 1 goto :path_error

for /f "delims=" %%i in ('dir /b /ad /o-d "output" 2^>nul') do (
  if exist "output\%%i\weekly_dance_top100.html" start "" "output\%%i\weekly_dance_top100.html"
  start "" "output\%%i"
  exit /b 0
)

echo No output folder was found. Run run_weekly.bat first.
pause
exit /b 1

:path_error
echo ERROR: Cannot enter the tool folder.
pause
exit /b 1
