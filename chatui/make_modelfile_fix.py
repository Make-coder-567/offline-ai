# -*- coding: utf-8 -*-
"""生成"接收方急救补丁包" modelfile_fix.zip (仅几 KB)。

背景: 首批分发的 offline_chat.zip 中, PyInstaller spec 只打包了 9 个 GGUF,
漏掉了每个模型的 Modelfile(ollama create 必需) → 接收方首次运行时
9 个模型全部被"跳过(无 Modelfile)", 无法安装模型。

本补丁包让已收到大包的用户免于重新下载 14.5GB。

v2 修复: v1 的 fix_models.bat 位于 modelfile_fix 子文件夹内, 却只检查
同级的 _internal\\bundle_models\\, 结果永远误报 "Wrong location"。
v2 改为:
  1. 自动探测 offline_chat 根目录 (bat 同级 / 上一级 / 上两级);
  2. 探测不到时提示用户"把 offline_chat 文件夹拖进来"或手输路径;
  3. 拷完再做一次存在性复核(9/9), 并写 fix_models_result.txt 备查。

用法: python make_modelfile_fix.py   (在 chatui 目录下运行)
产物: dist/modelfile_fix.zip
"""
import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_BUNDLE = os.path.join(HERE, "bundle_models")
OUT_DIR = os.path.join(HERE, "dist", "modelfile_fix")
OUT_ZIP = os.path.join(HERE, "dist", "modelfile_fix.zip")

FIX_BAT = r"""@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "SELF=%~dp0"
set "RES=%~dp0fix_models_result.txt"
echo ===== modelfile_fix v2 ===== > "%RES%"
echo Date: %date% %time% >> "%RES%"
echo Bat dir: %SELF% >> "%RES%"

if not exist "%SELF%modelfiles\" goto :nofiles

REM ---- 1) locate the offline_chat folder (contains _internal\bundle_models) ----
set "ROOT="
if exist "%SELF%_internal\bundle_models\" set "ROOT=%SELF%"
if not defined ROOT if exist "%SELF%..\_internal\bundle_models\" set "ROOT=%SELF%..\"
if not defined ROOT if exist "%SELF%..\..\_internal\bundle_models\" set "ROOT=%SELF%..\..\"
if defined ROOT goto :gotroot

echo.
echo [1/2] Could not find the offline_chat folder automatically.
echo.
echo   Drag the "offline_chat" folder into this window and press Enter,
echo   or type its full path (example:  D:\offline_chat ) then Enter.
echo   ^(The folder must contain offline_chat.exe and the _internal folder^)
echo.
set "ROOT="
set /p "ROOT=  offline_chat path: "
set "ROOT=!ROOT:"=!"
if "!ROOT:~-1!"=="\" set "ROOT=!ROOT:~0,-1!"
if not exist "!ROOT!\_internal\bundle_models\" goto :badpath

:gotroot
echo Found offline_chat folder: %ROOT%
echo Found offline_chat folder: %ROOT% >> "%RES%"
echo.

REM ---- 2) copy the 9 Modelfiles ----
echo [2/2] Copying 9 Modelfiles ...
set /a CNT=0
for /d %%D in ("%SELF%modelfiles\*") do (
  if not exist "%ROOT%\_internal\bundle_models\%%~nxD\" mkdir "%ROOT%\_internal\bundle_models\%%~nxD"
  copy /y "%%~fD\Modelfile" "%ROOT%\_internal\bundle_models\%%~nxD\Modelfile" >nul
  if errorlevel 1 (
    echo   FAILED: %%~nxD
    echo FAILED: %%~nxD >> "%RES%"
  ) else (
    set /a CNT+=1
    echo   OK: %%~nxD
    echo OK: %%~nxD >> "%RES%"
  )
)
echo.
echo Copied !CNT! of 9 Modelfiles.
echo Copied !CNT! of 9 Modelfiles >> "%RES%"

set /a VCNT=0
for /d %%D in ("%ROOT%\_internal\bundle_models\*") do (
  if exist "%%~fD\Modelfile" set /a VCNT+=1
)
echo   Modelfiles now present in the package: !VCNT! / 9
echo Modelfiles now present: !VCNT! / 9 >> "%RES%"

if not "!CNT!"=="9" goto :copyfail
if not "!VCNT!"=="9" goto :copyfail

echo.
echo ============================================================
echo  DONE!  Next steps:
echo    1. Close this window.
echo    2. Double-click offline_chat.exe again.
echo    3. First launch imports 9 models automatically
echo       - needs about 15 GB free space on drive C:
echo       - takes about 5-20 minutes
echo       - do NOT close the window while it says "importing models"
echo ============================================================
echo.
echo If it still fails, run diagnose.bat and send back
echo diagnose_result.txt + offline_chat_debug.log
echo.
pause
exit /b 0

:nofiles
echo [ERROR] "modelfiles" folder is missing next to this bat.
echo         Please re-extract modelfile_fix.zip and run again.
echo [ERROR] modelfiles folder missing >> "%RES%"
pause
exit /b 1

:badpath
echo.
echo [ERROR] Not found: !ROOT!\_internal\bundle_models\
echo         Check the path - it must contain offline_chat.exe and _internal.
echo         Then run fix_models.bat again.
echo [ERROR] bad path: !ROOT! >> "%RES%"
pause
exit /b 1

:copyfail
echo.
echo [ERROR] Copy incomplete. Right-click fix_models.bat and choose
echo         "Run as administrator", then try again.
echo [ERROR] copy incomplete >> "%RES%"
pause
exit /b 1
""".replace("\n", "\r\n")

README = """【离线AI聊天工具 · 模型修复补丁 v2】

问题：您收到的安装包缺少 9 个很小的"Modelfile"配置文件，导致模型无法自动安装。
本补丁只需几秒钟补齐，无需重新下载 14GB 大包。

—— 方法一（推荐）——
1. 把整个 modelfile_fix 文件夹复制到 offline_chat 文件夹里面
   （offline_chat 就是包含 offline_chat.exe 和 _internal 的那个文件夹）。
2. 双击 fix_models.bat。
   · 正常情况下它会自动找到 offline_chat 目录并开始复制；
   · 如果提示 "Could not find the offline_chat folder automatically"，
     就把 offline_chat 文件夹直接拖进黑色窗口、按回车；或手动输入它的
     完整路径（例如 D:\\offline_chat）再按回车。
3. 看到 "Copied 9 of 9 Modelfiles" 和 "DONE!" 即成功。
4. 回到 offline_chat 文件夹，重新双击 offline_chat.exe：
   · 首次运行会自动导入 9 个模型（需要 C 盘约 15GB 空闲空间，约 5~20 分钟）
   · 窗口显示"正在导入内置模型"时请不要关闭窗口

—— 方法二（脚本仍失败时，全手动，100% 可行）——
1. 打开 modelfile_fix\\modelfiles\\ ，里面有 9 个文件夹（qwen3-0.6b、gemma3-1b …）。
2. 打开 offline_chat\\_internal\\bundle_models\\ ，里面也有同名的 9 个文件夹。
3. 逐个把 modelfiles\\<模型名>\\Modelfile 这个文件，复制进
   _internal\\bundle_models\\<相同模型名>\\ 里（每个文件夹一个 Modelfile）。
4. 完成后重新双击 offline_chat.exe。

如果仍然失败：双击 offline_chat 文件夹里的 diagnose.bat，
把生成的 diagnose_result.txt、fix_models_result.txt 和 offline_chat_debug.log
发回给发送人即可定位。
"""


def main():
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    if os.path.isfile(OUT_ZIP):
        os.remove(OUT_ZIP)

    copied = 0
    for name in sorted(os.listdir(SRC_BUNDLE)):
        src = os.path.join(SRC_BUNDLE, name, "Modelfile")
        if not os.path.isfile(src):
            raise SystemExit("缺少源 Modelfile: %s" % src)
        dst = os.path.join(OUT_DIR, "modelfiles", name, "Modelfile")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print("  + modelfiles/%s/Modelfile" % name)

    with open(os.path.join(OUT_DIR, "fix_models.bat"), "w",
              encoding="ascii", newline="") as f:
        f.write(FIX_BAT)
    with open(os.path.join(OUT_DIR, "README.txt"), "w",
              encoding="utf-8-sig", newline="\r\n") as f:
        f.write(README)
    print("已写入 fix_models.bat / README.txt; Modelfile 共 %d 个" % copied)

    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUT_DIR):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.join("modelfile_fix",
                                   os.path.relpath(full, OUT_DIR))
                z.write(full, arc)
    print("已生成: %s (%.1f KB)" % (OUT_ZIP, os.path.getsize(OUT_ZIP) / 1024))

    raw = open(os.path.join(OUT_DIR, "fix_models.bat"), "rb").read()
    assert raw.count(b"\r\n") == raw.count(b"\n"), "bat 换行必须是 CRLF"
    raw.decode("ascii")
    with zipfile.ZipFile(OUT_ZIP) as z:
        assert z.testzip() is None, "zip 损坏"
        names = z.namelist()
        assert len([n for n in names if n.endswith("/Modelfile")]) == 9, "Modelfile 数不是 9"
    print("自校验通过: CRLF/ASCII/zip 完整性/9 个 Modelfile 均正常")


if __name__ == "__main__":
    main()
