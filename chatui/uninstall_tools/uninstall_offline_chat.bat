@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  offline_chat  FULL UNINSTALLER  (v3)
REM
REM  Removes: the app folder (incl. built-in models), the portable
REM  ollama copy, all imported models (drive C: and the app drive),
REM  the OLLAMA_MODELS environment variable, logs and shortcuts.
REM
REM  Just double-click it. It locates the offline_chat folder by
REM  itself (next to this file, one/two levels up, Desktop /
REM  Downloads / Documents, or any drive root). If it cannot,
REM  it asks you to drag the offline_chat folder into the window.
REM
REM  v3 rewrite: paths containing "(" ")" "&" "!" or spaces can no
REM  longer break the batch parsing (no %VAR% inside ( ) blocks,
REM  goto-only flow, delayed expansion everywhere else).
REM ============================================================

set "SELFDIR=%~dp0"
if "!SELFDIR:~-1!"=="\" set "SELFDIR=!SELFDIR:~0,-1!"
set "SELFBAT=%~f0"
set "UP=%USERPROFILE%"
set "LP=%LOCALAPPDATA%"
set "AD=%APPDATA%"
set "TARGET=%TEMP%\_uc_target.txt"
set "TMPCOPY=%TEMP%\_uninstall_offline_chat.bat"
set "LOGF=!SELFDIR!\uninstall_log.txt"
set "RUNSFROMINSIDE=0"
set "APPDIR="

> "!LOGF!" echo ===== offline_chat uninstaller log =====
>> "!LOGF!" echo date: %date% %time%
>> "!LOGF!" echo bat : !SELFBAT!

if /i "%~1"=="/FROMTEMP" goto :WORK

REM ============ locate the offline_chat folder ============
:LOCATE
if exist "!SELFDIR!\offline_chat.exe" goto :GOT_INPLACE
if exist "!SELFDIR!\..\offline_chat.exe" goto :GOT_PARENT
if exist "!SELFDIR!\..\..\offline_chat.exe" goto :GOT_GRANDPARENT
if exist "!UP!\Desktop\offline_chat\offline_chat.exe" set "APPDIR=!UP!\Desktop\offline_chat"
if not defined APPDIR if exist "!UP!\Downloads\offline_chat\offline_chat.exe" set "APPDIR=!UP!\Downloads\offline_chat"
if not defined APPDIR if exist "!UP!\Documents\offline_chat\offline_chat.exe" set "APPDIR=!UP!\Documents\offline_chat"
if not defined APPDIR for %%R in (C D E F G H) do if exist "%%R:\offline_chat\offline_chat.exe" set "APPDIR=%%R:\offline_chat"
if defined APPDIR goto :LIST

echo.
echo   Could not find the offline_chat folder automatically.
echo   Drag the "offline_chat" folder into this window and press Enter,
echo   or type its full path ^(example: D:\offline_chat^) then Enter.
echo.
set "APPDIR="
set /p "APPDIR=offline_chat path: "
if defined APPDIR set "APPDIR=!APPDIR:"=!"
if defined APPDIR if "!APPDIR:~-1!"=="\" set "APPDIR=!APPDIR:~0,-1!"
if exist "!APPDIR!\offline_chat.exe" goto :LIST
goto :NOPATH

:GOT_INPLACE
set "APPDIR=!SELFDIR!"
set "RUNSFROMINSIDE=1"
goto :LIST

:GOT_PARENT
pushd "!SELFDIR!\.."
set "APPDIR=!CD!"
popd
goto :LIST

:GOT_GRANDPARENT
pushd "!SELFDIR!\..\.."
set "APPDIR=!CD!"
popd
goto :LIST

:NOPATH
echo.
echo   [ERROR] Not found: "!APPDIR!\offline_chat.exe"
echo           Nothing was deleted. Press any key to exit.
>> "!LOGF!" echo ERROR path not found: !APPDIR!
pause >nul
exit /b 1

:CANCEL
echo.
echo   Cancelled. Nothing was deleted.
echo   Press any key to exit.
>> "!LOGF!" echo cancelled by user
pause >nul
exit /b 0

REM ============ entry of the relaunched copy ============
:WORK
set "UC_YES=1"
if exist "!TARGET!" set /p APPDIR=<"!TARGET!"
if defined APPDIR if exist "!APPDIR!\offline_chat.exe" goto :LIST
goto :LOCATE

REM ============ plan + confirm ============
:LIST
for %%I in ("!APPDIR!") do set "APPDRIVE=%%~dI"
set "P_PORTABLE=!LP!\offline_chat_ollama"
set "P_STORE_C=!UP!\.ollama"
set "P_STORE_DRV=!APPDRIVE!\ollama_models"
set "P_ENV="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v OLLAMA_MODELS 2^>nul ^| findstr /i OLLAMA_MODELS') do set "P_ENV=%%B"
set "HAS_OFFICIAL="
if exist "!LP!\Programs\Ollama\ollama.exe" set "HAS_OFFICIAL=1"
set "P_LOGS="
if not defined HAS_OFFICIAL if exist "!LP!\Ollama" set "P_LOGS=!LP!\Ollama"

>> "!LOGF!" echo app dir      : !APPDIR!
>> "!LOGF!" echo from inside  : !RUNSFROMINSIDE!
>> "!LOGF!" echo models C:    : !P_STORE_C!
>> "!LOGF!" echo models drive : !P_STORE_DRV!
>> "!LOGF!" echo official oll : !HAS_OFFICIAL!
if defined P_ENV >> "!LOGF!" echo extra models : !P_ENV!

echo.
echo ============================================================
echo   The following will be PERMANENTLY deleted:
echo ============================================================
echo   [1] app folder       : !APPDIR!
echo   [2] portable ollama  : !P_PORTABLE!
set "L3=!P_STORE_C!"
if defined HAS_OFFICIAL set "L3=SKIPPED - official Ollama found"
echo   [3] models on C:     : !L3!
echo   [4] models on app drv: !P_STORE_DRV!
if defined P_ENV echo   [5] extra models dir : !P_ENV!
if defined P_LOGS echo   [6] ollama logs      : !P_LOGS!
echo   [7] OLLAMA_MODELS environment variable
echo   [8] shortcuts of this app on Desktop / Start Menu
echo.
echo   NOTE: if the official Ollama is installed on this PC, its
echo   model store is left untouched on purpose.
echo ============================================================
echo.
if defined UC_YES goto :CONFIRMED
set "ANS="
set /p "ANS=Type Y then Enter to continue: "
if /i not "!ANS!"=="Y" goto :CANCEL

:CONFIRMED
>> "!LOGF!" echo confirmed, deleting...
echo.
echo Stopping running processes ...
taskkill /f /im offline_chat.exe >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1
taskkill /f /im "ollama app.exe" >nul 2>&1
taskkill /f /im ollama_llama_server.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul

echo Removing OLLAMA_MODELS environment variable ...
reg delete "HKCU\Environment" /f /v OLLAMA_MODELS >nul 2>&1

echo Removing portable ollama copy ...
if exist "!P_PORTABLE!" rmdir /s /q "!P_PORTABLE!" >nul 2>&1
if exist "!P_PORTABLE!" >> "!LOGF!" echo WARN portable copy still exists

echo Removing model libraries ...
if exist "!P_STORE_DRV!" rmdir /s /q "!P_STORE_DRV!" >nul 2>&1
if defined P_ENV if exist "!P_ENV!" rmdir /s /q "!P_ENV!" >nul 2>&1
if not defined HAS_OFFICIAL if exist "!P_STORE_C!" rmdir /s /q "!P_STORE_C!" >nul 2>&1

echo Removing ollama logs ...
if defined P_LOGS if exist "!P_LOGS!" rmdir /s /q "!P_LOGS!" >nul 2>&1

echo Removing shortcuts ...
del /f /q "!UP!\Desktop\offline_chat.lnk" >nul 2>&1
del /f /q "!UP!\Desktop\OfflineChat.lnk" >nul 2>&1
del /f /q "!AD!\Microsoft\Windows\Start Menu\Programs\offline_chat.lnk" >nul 2>&1

if "!RUNSFROMINSIDE!"=="1" goto :RELAUNCH_FOR_FOLDER
echo Removing the app folder ^(large, may take a while^) ...
rmdir /s /q "!APPDIR!" >nul 2>&1
goto :RESULT

:RELAUNCH_FOR_FOLDER
echo.
echo   This tool sits inside the app folder, so the folder itself
echo   will be removed from a temporary copy: a NEW black window
echo   will open and finish the job.
> "!TARGET!" echo !APPDIR!
copy /y "!SELFBAT!" "!TMPCOPY!" >nul 2>&1
if not exist "!TMPCOPY!" goto :FOLDER_INUSE
start "" "!TMPCOPY!" /FROMTEMP
exit /b 0

:FOLDER_INUSE
echo.
echo   [WARN] The temporary copy could not be created, so the app
echo          folder was NOT deleted. Please delete this folder by
echo          hand:  !APPDIR!
echo   Press any key to exit.
>> "!LOGF!" echo WARN folder not deleted (no temp copy)
pause >nul
exit /b 1

REM ============ result ============
:RESULT
set "M1=[ok]"
set "S1=removed"
if exist "!APPDIR!" set "M1=[X ]"
if exist "!APPDIR!" set "S1=STILL EXISTS"
set "M2=[ok]"
set "S2=removed"
if exist "!P_PORTABLE!" set "M2=[X ]"
if exist "!P_PORTABLE!" set "S2=STILL EXISTS"
set "M3=[ok]"
set "S3=removed"
if defined HAS_OFFICIAL set "M3=[--]"
if defined HAS_OFFICIAL set "S3=skipped - official Ollama"
if not defined HAS_OFFICIAL if exist "!P_STORE_C!" set "M3=[X ]"
if not defined HAS_OFFICIAL if exist "!P_STORE_C!" set "S3=STILL EXISTS"
set "M4=[ok]"
set "S4=removed"
if exist "!P_STORE_DRV!" set "M4=[X ]"
if exist "!P_STORE_DRV!" set "S4=STILL EXISTS"
set "M5=[ok]"
set "S5=removed"
for /f "tokens=*" %%A in ('reg query "HKCU\Environment" /v OLLAMA_MODELS 2^>nul') do set "M5=[X ]"
if "!M5!"=="[X ]" set "S5=STILL SET"

echo.
echo ============================================================
echo   RESULT
echo ============================================================
echo   !M1! app folder        : !S1!
echo   !M2! portable ollama   : !S2!
echo   !M3! models on C:      : !S3!
echo   !M4! models on app drv : !S4!
echo   !M5! OLLAMA_MODELS     : !S5!
>> "!LOGF!" echo RESULT app=!M1! portable=!M2! storeC=!M3! storeDrv=!M4! env=!M5!
echo.
powershell -NoProfile -Command "try{$d=Get-PSDrive -Name ('!APPDRIVE!' -replace ':',''); Write-Host ('  Free space on !APPDRIVE! now : ' + [math]::Round($d.Free/1GB,1) + ' GB')}catch{}"
echo.
echo All done. Please REBOOT (or log off and on) once so the
echo environment change takes full effect.
echo If any line above shows [X ], close all ollama/chat windows,
echo reboot, and run this uninstaller one more time.
echo.
set "BADS="
if "!M1!"=="[X ]" set "BADS=1"
if "!M2!"=="[X ]" set "BADS=1"
if "!M4!"=="[X ]" set "BADS=1"
if "!M5!"=="[X ]" set "BADS=1"
if defined BADS goto :KEEP_LOG
>> "!LOGF!" echo all ok
del /f /q "!LOGF!" >nul 2>&1
goto :FINISH

:KEEP_LOG
echo A log file was written here (send it back if something failed):
echo   !LOGF!

:FINISH
if exist "!TARGET!" del /f /q "!TARGET!" >nul 2>&1
echo.
echo Press any key to close this window ...
pause >nul
del /f /q "!SELFBAT!" >nul 2>&1
exit /b 0
