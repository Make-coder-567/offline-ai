# -*- mode: python ; coding: utf-8 -*-

import json
import os
import sys

try:
    SPEC_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # PyInstaller 某些调用方式未在 spec 命名空间注入 __file__, 退回当前工作目录
    SPEC_DIR = os.getcwd()

# OFFLINE_LITE=1 → 只打"轻装版"(连本机已有 Ollama, 不带模型与运行时)
LITE = os.environ.get('OFFLINE_LITE') == '1'

# 图标 + 便携 Ollama(轻装版不带)
datas = [('app.ico', '.')]
if not LITE:
    datas.append(('ollama', 'ollama'))

# 应用代码与离线资源：
#   server/  → docparse / rag / embed / audit / memory / scheduler / gateway / paths
#   model_assets/ → bge-small-zh 向量(94MB) + piper 语音(63MB) + whisper-base(145MB)
datas.append((os.path.join(os.path.dirname(SPEC_DIR), 'server'), 'server'))
datas.append((os.path.join(os.path.dirname(SPEC_DIR), 'config.yaml'), '.'))
if not LITE and os.path.isdir(os.path.join(SPEC_DIR, 'model_assets')):
    datas.append(('model_assets', 'model_assets'))

# 离线模型包: 由 prepare_bundle.py 生成 bundle_manifest.json + bundle_models/*/Modelfile
manifest_path = os.path.join(SPEC_DIR, 'bundle_manifest.json')


def _find_model_file(path_hint, safe, name):
    """按优先级找模型文件：manifest 记录的路径 → bundle_models → 上一版构建产物。

    源码权重（chatui/gguf_models）体积太大，可能已被清理；此时直接复用上一次
    构建包里已经拷好的 GGUF，避免为了改几行代码重新下载 19GB。
    """
    base = os.path.basename(path_hint or name or "")
    cands = [path_hint] if path_hint else []
    cands += [
        os.path.join(SPEC_DIR, 'bundle_models', safe, base),
        os.path.join(SPEC_DIR, 'dist', '_old', '_internal', 'bundle_models', safe, base),
        os.path.join(SPEC_DIR, 'dist', 'offline_chat', '_internal', 'bundle_models', safe, base),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None

if os.path.isfile(manifest_path) and not LITE:
    with open(manifest_path, encoding='utf-8') as f:
        _manifest = json.load(f)
    datas.append(('bundle_manifest.json', '.'))
    _miss = []
    for _m in _manifest:
        # 每个模型两条: GGUF 权重 + Modelfile(ollama create 必需, 缺一个都装不上)
        _gguf = _find_model_file(_m.get('gguf_abs'), _m['safe'], _m.get('gguf_name'))
        if not _gguf:
            _miss.append(_m['safe'])
            continue
        datas.append((_gguf, os.path.join('bundle_models', _m['safe'])))
        datas.append((os.path.join(SPEC_DIR, 'bundle_models', _m['safe'], 'Modelfile'),
                      os.path.join('bundle_models', _m['safe'])))
        # 视觉模型额外带 mmproj 投影文件（Modelfile 里第二条 FROM）
        if _m.get('mmproj_abs') or _m.get('mmproj_name'):
            _mp = _find_model_file(_m.get('mmproj_abs'), _m['safe'], _m.get('mmproj_name'))
            if _mp:
                datas.append((_mp, os.path.join('bundle_models', _m['safe'])))
            else:
                _miss.append(_m['safe'] + '/mmproj')
    if _miss:
        raise SystemExit("缺少模型文件，无法打包：%s\n"
                         "请先运行 pull_models.py 下载，或保留上一版 dist 产物以便复用。"
                         % ", ".join(_miss))
    print("已纳入 %d 个离线模型(GGUF+Modelfile 各 %d)到打包数据。"
          % (len(_manifest), len(_manifest)))
elif LITE:
    print("OFFLINE_LITE=1: 轻装版, 不内置离线模型与便携 Ollama。")
else:
    print("WARNING: bundle_manifest.json 不存在, 本次构建将不包含离线模型! "
          "请先运行 prepare_bundle.py。")

# 动态 import 的模块必须显式列出, 否则打包后运行时报 ModuleNotFoundError
# (chat_app 里的 docparse / voice / sessions 都是懒加载, PyInstaller 静态分析看不到)
HIDDEN = [
    'sessions', 'voice', 'theme',
    'server', 'server.docparse', 'server.rag', 'server.embed', 'server.audit',
    'server.memory', 'server.scheduler', 'server.gateway', 'server.paths',
    'server.tools', 'server.agent', 'server.mcp_server',
    # 文档解析
    'pypdf', 'docx', 'openpyxl', 'pptx', 'pypdfium2', 'rapidocr_onnxruntime',
    # 语义向量 / 语音（模型来自 model_assets，库在这里）
    'fastembed', 'piper', 'faster_whisper', 'sounddevice',
]

# 注意: 因内置模型约 14GB, 超过 PyInstaller 单文件(onefile)归档的 4GB 偏移上限,
# 故采用 onedir(文件夹) 分发。最终把 dist/offline_chat 整体打包(zip)发送即可。
# pathex 必须带上项目根：否则 hiddenimports 里的 server.* 找不到（server 是源码外的包）
PROJECT_ROOT = os.path.dirname(SPEC_DIR)
a = Analysis(
    ['chat_app.py'],
    pathex=[SPEC_DIR, PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=HIDDEN,
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
# 源码目录是以 datas 整目录拷进来的, 会带上 __pycache__ (跨版本残留 .pyc 既占体积也易误导入)
a.datas = [d for d in a.datas if "__pycache__" not in str(d[0]).replace("\\", "/").split("/")]

pyz = PYZ(a.pure)

if LITE:
    # 轻装版：单文件 exe（不带模型，体积几十 MB），连本机已有的 Ollama 服务
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='offline_chat_lite',
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
else:
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
