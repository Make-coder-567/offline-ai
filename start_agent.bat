@echo off
chcp 65001 >nul
REM Offline AI agent launcher - watches .offline-bridge/inbox, calls local Ollama
REM 解释器优先级：OFFLINE_AI_PYTHON 环境变量 > PATH 里的 python > py -3
set "AGENT=%~dp0server\agent.py"
if not exist "%AGENT%" (
    echo [error] 找不到 %AGENT%
    pause
    exit /b 1
)
if defined OFFLINE_AI_PYTHON (
    echo [offline-agent] python: %OFFLINE_AI_PYTHON%
    "%OFFLINE_AI_PYTHON%" "%AGENT%"
    goto :eof
)
where python >nul 2>&1
if not errorlevel 1 (
    echo [offline-agent] python: PATH
    python "%AGENT%"
    goto :eof
)
where py >nul 2>&1
if not errorlevel 1 (
    echo [offline-agent] python: py -3
    py -3 "%AGENT%"
    goto :eof
)
echo [error] 未找到 Python。请安装 Python 3.10+，或设置 OFFLINE_AI_PYTHON 指向解释器。
pause
