@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "%~dp0offline_chat.exe" goto :wrongplace

REM ============================================================
REM  Where to store the 9 models (total about 14 GB).
REM  Default = the SAME drive as this program.
REM  If that drive is short of free space, edit the line below,
REM  for example:   set "MODELS_DRIVE=E:"
REM ============================================================
set "MODELS_DRIVE=%~d0"

set "OLLAMA_MODELS=%MODELS_DRIVE%\ollama_models"
echo.
echo Target model folder : %OLLAMA_MODELS%
powershell -NoProfile -Command "try{$d=Get-PSDrive -Name ('%MODELS_DRIVE%' -replace ':',''); Write-Host ('Free space on %MODELS_DRIVE% : ' + [math]::Round($d.Free/1GB,1) + ' GB')}catch{}"
echo   (need about 15 GB free there; if not enough: press Ctrl+C,
echo    edit MODELS_DRIVE in this file with Notepad, run again)
echo.
timeout /t 6

if not exist "%OLLAMA_MODELS%" mkdir "%OLLAMA_MODELS%" >nul 2>&1

echo Stopping any running chat app / ollama first ...
taskkill /im offline_chat.exe /f >nul 2>&1
taskkill /im ollama.exe /f >nul 2>&1
timeout /t 2 /nobreak >nul

setx OLLAMA_MODELS "%OLLAMA_MODELS%" >nul

echo.
echo Starting the chat app, please wait ...
echo First launch imports all 9 models into the folder above (5-20 minutes).
echo Do NOT close the chat window while it says "importing models".
echo.
start "" "%~dp0offline_chat.exe"
echo.
echo Done. You may close THIS black window now.
timeout /t 8 /nobreak >nul
exit /b 0

:wrongplace
echo [ERROR] Wrong location.
echo         Put this .bat INSIDE the offline_chat folder
echo         (the folder containing offline_chat.exe), then double-click again.
pause
exit /b 1
