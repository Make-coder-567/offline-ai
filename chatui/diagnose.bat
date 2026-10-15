@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "LOG=%~dp0diagnose_result.txt"
echo ===== Offline Chat Diagnostic ===== > "%LOG%"
echo Date: %date% %time% >> "%LOG%"
echo Dir : %CD% >> "%LOG%"
echo. >> "%LOG%"

set "OLL=__internal\ollama\ollama.exe"
if not exist "%OLL%" (
  echo [ERROR] Cannot find %OLL% >> "%LOG%"
  echo Put diagnose.bat INSIDE the offline_chat folder ^(same level as _internal^). >> "%LOG%"
  goto :done
)

echo [1] Modelfiles in _internal\bundle_models (expect 9 found): >> "%LOG%"
set "MCNT=0"
for /d %%D in (__internal\bundle_models\*) do (
  if exist "%%D\Modelfile" (
    echo   %%~nxD : Modelfile OK >> "%LOG%"
    set /a MCNT+=1
  ) else (
    echo   %%~nxD : Modelfile MISSING >> "%LOG%"
  )
)
echo   modelfiles found = !MCNT! (expect 9) >> "%LOG%"
echo. >> "%LOG%"

echo [2] Built-in GGUF files (expect 9, each > 100MB): >> "%LOG%"
set "CNT=0"
for /d %%D in (__internal\bundle_models\*) do (
  for %%F in ("%%D\*.gguf") do (
    echo   %%~nxF  size=%%~zF >> "%LOG%"
    set /a CNT+=1
  )
)
echo   total gguf files = !CNT! (expect 9) >> "%LOG%"
echo. >> "%LOG%"

echo [3] Start ollama serve (port 11439, isolated) ... >> "%LOG%"
set "OLLAMA_MODELS=%~dp0_ollama_diag_models"
set "OLLAMA_HOST=127.0.0.1:11439"
if exist "%OLLAMA_MODELS%" rmdir /s /q "%OLLAMA_MODELS%" >nul 2>&1
start "" /min cmd /c ""%OLL%" serve >> "%~dp0serve_diag.log" 2>&1"
timeout /t 8 /nobreak >nul

echo [4] Check port 11439 ... >> "%LOG%"
powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',11439); Write-Output '  PORT OPEN'; $c.Close() } catch { Write-Output ('  PORT CLOSED: ' + $_.Exception.Message) }" >> "%LOG%" 2>&1

echo [5] Try importing smallest model (qwen3:0.6b) ... >> "%LOG%"
if not exist "__internal\bundle_models\qwen3-0.6b\Modelfile" goto :nocreate
pushd __internal\bundle_models\qwen3-0.6b
"%OLL%" create diagtest -f Modelfile >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
popd
echo   create rc=%RC% >> "%LOG%"
goto :aftercreate
:nocreate
echo   SKIPPED: Modelfile missing for qwen3-0.6b >> "%LOG%"
:aftercreate
echo. >> "%LOG%"

echo [6] ollama list: >> "%LOG%"
"%OLL%" list >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [7] Cleanup test model dir ... >> "%LOG%"
if exist "%OLLAMA_MODELS%" rmdir /s /q "%OLLAMA_MODELS%" >nul 2>&1

:done
echo. >> "%LOG%"
echo ===== END ===== >> "%LOG%"
echo.
echo Diagnostic complete. Results saved to:
echo   %LOG%
echo Please send BOTH files back to the sender:
echo   diagnose_result.txt  +  serve_diag.log
echo   (also offline_chat_debug.log if it exists)
echo.
pause
