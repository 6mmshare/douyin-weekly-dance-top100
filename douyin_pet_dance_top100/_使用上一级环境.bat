@echo off
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (echo [ERROR] Parent .venv not found: %PYTHON%&exit /b 1)
exit /b 0
