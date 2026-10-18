@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 切换到新构建的离线AI包

echo ============================================================
echo  作用：把 dist_new\offline_chat（新构建）替换掉 dist\offline_chat
echo  前提：请先关闭正在运行的 offline_chat.exe
echo ============================================================
echo.
tasklist /FI "IMAGENAME eq offline_chat.exe" 2>nul | find /I "offline_chat.exe" >nul
if not errorlevel 1 (
    echo [提示] 检测到 offline_chat.exe 还在运行，请先关闭窗口再运行本脚本。
    echo.
    pause
    exit /b 1
)

if not exist "dist_new\offline_chat\offline_chat.exe" (
    echo [错误] 找不到 dist_new\offline_chat\offline_chat.exe，无需替换。
    pause
    exit /b 1
)

if exist "dist\offline_chat" (
    echo 正在移除旧包 dist\offline_chat ...
    rmdir /s /q "dist\offline_chat"
    if exist "dist\offline_chat" (
        echo [错误] 旧包删不掉（可能仍有进程占用），请重启电脑后再试。
        pause
        exit /b 1
    )
)

echo 正在搬入新包 ...
move "dist_new\offline_chat" "dist\offline_chat" >nul
if not exist "dist\offline_chat\offline_chat.exe" (
    echo [错误] 替换失败，请手动把 dist_new\offline_chat 改名为 dist\offline_chat。
    pause
    exit /b 1
)
rmdir "dist_new" 2>nul

echo.
echo [完成] 新包已在 dist\offline_chat，双击 offline_chat.exe 即可使用。
echo        原有的模型库（.ollama）不会被改动，无需重新导入。
pause
