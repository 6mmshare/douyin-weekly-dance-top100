@echo off
chcp 65001 >nul
cd /d "%~dp0"
for /f "delims=" %%D in ('dir /b /ad /o-d "output" 2^>nul') do (if exist "output\%%D\weekly_dance_top100.html" (start "" "output\%%D\weekly_dance_top100.html"&exit /b 0))
echo No result found.
pause
