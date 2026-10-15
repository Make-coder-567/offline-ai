@echo off
REM Offline AI agent launcher - watches .offline-bridge/inbox, calls local Ollama
set "PY=C:\Users\mrhua\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "AGENT=E:\Workbuddy\2026-10-11-17-27-15\offline-ai\server\agent.py"
if not exist "%PY%" (
    echo [error] venv python not found: %PY%
    pause
    exit /b 1
)
echo [offline-agent] launching... (Ctrl+C to stop)
"%PY%" "%AGENT%"
