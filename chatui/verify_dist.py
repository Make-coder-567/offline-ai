# -*- coding: utf-8 -*-
"""构建产物自校验: 任何一项不过即退出非零(后台打包链会因此中止, 不产出坏包)。

校验项:
  1. offline_chat.exe / _internal/ollama/ollama.exe / bundle_manifest.json 存在
  2. 9 个模型: Modelfile 存在 + GGUF 存在且 >1MB   ← 本轮修复的核心
  3. _internal 根目录无 Ollama 运行库污染(ggml/libllama/libc++ 等)  ← 本轮修复
  4. _internal/ollama/lib/ollama/ 合法副本未被误删
  5. diagnose.bat 已就位且为 CRLF
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist", "offline_chat")
INT = os.path.join(DIST, "_internal")

fails = []

def need(cond, msg):
    if not cond:
        fails.append(msg)
        print("  [FAIL] " + msg)
    else:
        print("  [ OK ] " + msg)

print("== 1. 关键文件 ==")
need(os.path.isfile(os.path.join(DIST, "offline_chat.exe")), "offline_chat.exe")
need(os.path.isfile(os.path.join(INT, "ollama", "ollama.exe")), "_internal/ollama/ollama.exe")
need(os.path.isfile(os.path.join(INT, "bundle_manifest.json")), "bundle_manifest.json")

print("== 2. 9 个模型: Modelfile + GGUF ==")
with open(os.path.join(INT, "bundle_manifest.json"), encoding="utf-8") as f:
    manifest = json.load(f)
need(len(manifest) == 9, "manifest 共 9 个模型 (实际 %d)" % len(manifest))
for m in manifest:
    d = os.path.join(INT, "bundle_models", m["safe"])
    mf = os.path.join(d, "Modelfile")
    gg = os.path.join(d, m["gguf_name"])
    need(os.path.isfile(mf), "%s/Modelfile" % m["safe"])
    ok_gg = os.path.isfile(gg) and os.path.getsize(gg) > 1024 * 1024
    need(ok_gg, "%s/%s (>1MB)" % (m["safe"], m["gguf_name"]))

print("== 3. _internal 根目录 Ollama 污染 DLL ==")
POLLUTION = ["ggml-base.dll", "ggml.dll", "libllama.dll", "libllama-common.dll",
             "libllama-server-impl.dll", "libc++.dll", "libunwind.dll",
             "libomp.dll", "libmtmd.dll"]
found = [p for p in POLLUTION if os.path.isfile(os.path.join(INT, p))]
need(not found, "根目录无污染 DLL (发现: %s)" % (found or "无"))

print("== 4. 便携 Ollama 自带库未被误删 ==")
need(os.path.isfile(os.path.join(INT, "ollama", "lib", "ollama", "ggml-base.dll")),
     "_internal/ollama/lib/ollama/ggml-base.dll 仍在")

print("== 5. 随包辅助文件 ==")
for extra in ["diagnose.bat", "start_models_on_D.bat", "uninstall_offline_chat.bat",
              "离线AI聊天工具_使用说明.docx"]:
    need(os.path.isfile(os.path.join(DIST, extra)), extra)
diag = os.path.join(DIST, "diagnose.bat")
if os.path.isfile(diag):
    raw = open(diag, "rb").read()
    need(b"\r\n" in raw and raw.replace(b"\r\n", b"").find(b"\n") == -1,
         "diagnose.bat 为 CRLF 换行")
for extra in ["start_models_on_D.bat", "uninstall_offline_chat.bat"]:
    p = os.path.join(DIST, extra)
    if os.path.isfile(p):
        raw = open(p, "rb").read()
        need(b"\r\n" in raw and raw.replace(b"\r\n", b"").find(b"\n") == -1,
             "%s 为 CRLF 换行" % extra)

if fails:
    print("\n校验未通过 %d 项, 中止打包!" % len(fails))
    sys.exit(1)
print("\n全部校验通过, 可以打 zip。")
