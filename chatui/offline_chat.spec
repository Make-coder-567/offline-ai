# -*- mode: python ; coding: utf-8 -*-

import json
import os
import sys

try:
    SPEC_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # PyInstaller 某些调用方式未在 spec 命名空间注入 __file__, 退回当前工作目录
    SPEC_DIR = os.getcwd()

# 默认打包 Ollama 程序 + 图标
datas = [('ollama', 'ollama'), ('app.ico', '.')]

# 离线模型包: 由 prepare_bundle.py 生成 bundle_manifest.json + bundle_models/*/Modelfile
manifest_path = os.path.join(SPEC_DIR, 'bundle_manifest.json')
if os.path.isfile(manifest_path):
    with open(manifest_path, encoding='utf-8') as f:
        _manifest = json.load(f)
    datas.append(('bundle_manifest.json', '.'))
    for _m in _manifest:
        # 每个模型两条: GGUF 权重 + Modelfile(ollama create 必需, 缺一个都装不上)
        datas.append((_m['gguf_abs'], os.path.join('bundle_models', _m['safe'])))
        datas.append((os.path.join(SPEC_DIR, 'bundle_models', _m['safe'], 'Modelfile'),
                      os.path.join('bundle_models', _m['safe'])))
    print("已纳入 %d 个离线模型(GGUF+Modelfile 各 %d)到打包数据。"
          % (len(_manifest), len(_manifest)))
else:
    print("WARNING: bundle_manifest.json 不存在, 本次构建将不包含离线模型! "
          "请先运行 prepare_bundle.py。")

# 注意: 因内置模型约 14GB, 超过 PyInstaller 单文件(onefile)归档的 4GB 偏移上限,
# 故采用 onedir(文件夹) 分发。最终把 dist/offline_chat 整体打包(zip)发送即可。
a = Analysis(
    ['chat_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# 剔除 PyInstaller 误收集的便携 Ollama 运行库:
# Analysis 会顺带分析 datas 里 ollama.exe 的依赖, 把 chatui/ollama/lib/ollama 下的
# ggml*.dll / libllama*.dll / libc++.dll / libunwind.dll / libomp.dll / libmtmd.dll
# 等一并收进 a.binaries 且目标为 _internal 根目录(与 _internal/ollama/ 里的合法副本
# 重复, 还会因 PyInstaller 把 _internal 前置进 PATH 而干扰 ollama serve 的库查找,
# 触发 "potentially incompatible library detected" 警告)。
# 本应用(纯 HTTP 客户端)不加载这些库, 剔除根目录条目安全; 合法副本仍随 datas
# 完整保留在 _internal/ollama/lib/ollama/ 下。
_ollama_prefix = os.path.join(SPEC_DIR, 'ollama').lower().replace('/', os.sep) + os.sep

def _is_root_pollution(entry):
    dest, src = entry[0], entry[1]
    if os.path.dirname(dest):
        return False                       # 有子目录前缀 → 是 _internal/ollama/ 里的合法副本
    src_norm = src.lower().replace('/', os.sep)
    return src_norm.startswith(_ollama_prefix)

a.binaries = [b for b in a.binaries if not _is_root_pollution(b)]
a.datas = [d for d in a.datas if not _is_root_pollution(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='offline_chat',
    icon='app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='offline_chat',
)
