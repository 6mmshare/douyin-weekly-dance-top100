@echo off
rem 统一复用上一级 douyin-weekly-dance-top100-main\.venv
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo.
    echo [错误] 找不到上一级虚拟环境：
    echo %PYTHON%
    echo.
    echo 请确认当前工具目录结构为：
    echo douyin-weekly-dance-top100-main\
    echo ^|-- .venv\
    echo ^|-- douyin_creator_recent7d_tool\
    echo.
    exit /b 1
)

exit /b 0
