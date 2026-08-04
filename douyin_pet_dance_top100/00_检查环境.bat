@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_使用上一级环境.bat"
if errorlevel 1 (pause&exit /b 1)
"%PYTHON%" -c "import yaml,openpyxl,playwright;print('Environment OK')"
pause
