"""修复 qwen3:1.7b: 删除损坏的 Q8_0 缓存, 改用 Q4_K_M 重新下载并导入。

原 Q8_0 文件虽带 GGUF 头, 但 body 被截断 (ollama create 报
"offset+size exceeds file size")。这里:
  - 删掉任何已有的 1.7B gguf 缓存(避免 valid_gguf 只验头而误用坏文件)
  - 下载 Q4_K_M (CPU 上更小更快, ~1.1GB)
  - 额外校验下载大小 == Content-Length, 防截断
  - 写 Modelfile (qwen3 模板) 并 ollama create
"""
import os
import sys
import time
import json
import subprocess
import urllib.request

OLLAMA = r"C:\Users\mrhua\AppData\Local\Programs\Ollama\ollama.exe"
DL = r"E:\Workbuddy\2026-10-11-17-27-15\offline-ai\chatui\gguf_models"
os.makedirs(DL, exist_ok=True)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

QWEN3_TPL = (
    'TEMPLATE """{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n'
    '{{ end }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n'
    '<|im_start|>assistant\n{{ .Response }}<|im_end|>"""\n'
    'PARAMETER stop "<|im_start|>"\n'
    'PARAMETER stop "<|im_end|>"\n'
    'PARAMETER temperature 0.6\n'
    'PARAMETER top_p 0.95\n'
    'PARAMETER top_k 20\n'
)

REPO = "Qwen/Qwen3-1.7B-GGUF"
FNAME = "Qwen3-1.7B-Q8_0.gguf"
TAG = "qwen3:1.7b"


def human(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return "%.1f%s" % (n, u)
        n /= 1024.0
    return "%.1fTB" % n


def valid_gguf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"GGUF"
    except Exception:
        return False


# 1) 清掉损坏/旧缓存
for cand in ["Qwen3-1.7B-Q8_0.gguf", "Qwen3-1.7B-Q4_K_M.gguf"]:
    p = os.path.join(DL, cand)
    if os.path.exists(p):
        print("删除旧缓存: %s (%s)" % (cand, human(os.path.getsize(p))), flush=True)
        os.remove(p)

# 2) 下载 Q4_K_M
out = os.path.join(DL, FNAME)
url = ("https://modelscope.cn/api/v1/models/%s/repo"
       "?Revision=master&FilePath=%s" % (REPO, FNAME))
print("下载: %s/%s" % (REPO, FNAME), flush=True)
try:
    with OPENER.open(url, timeout=3600) as r, open(out, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        t0 = time.time()
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                spd = got / max(1e-6, time.time() - t0) / 1024 / 1024
                sys.stdout.write("\r  %.1f%%  %s / %s  (%.1f MB/s)"
                                 % (got * 100.0 / total, human(got),
                                    human(total), spd))
                sys.stdout.flush()
    print()
except Exception as e:
    print("下载异常: %r" % (e,), flush=True)
    sys.exit(1)

# 3) 校验: 头 + 大小
if not valid_gguf(out):
    print("GGUF 头校验失败", flush=True)
    os.remove(out)
    sys.exit(1)
if total and os.path.getsize(out) != total:
    print("大小不符(可能截断): 期望 %s 实际 %s"
          % (human(total), human(os.path.getsize(out))), flush=True)
    os.remove(out)
    sys.exit(1)
print("DL-OK %s" % human(os.path.getsize(out)), flush=True)

# 4) 写 Modelfile + create
mf = os.path.join(DL, "Modelfile.qwen3_1.7b")
with open(mf, "w", encoding="utf-8", newline="\n") as f:
    f.write("FROM " + out.replace("\\", "/") + "\n")
    f.write(QWEN3_TPL)
r = subprocess.run([OLLAMA, "create", TAG, "-f", mf],
                   capture_output=True, text=True, timeout=1800)
listing = subprocess.run([OLLAMA, "list"], capture_output=True, text=True)
if TAG in listing.stdout:
    print("[OK] 导入成功: %s" % TAG, flush=True)
else:
    print("[FAIL] create 失败:\n%s\n%s"
          % (r.stdout[-500:], r.stderr[-500:]), flush=True)
    sys.exit(1)
