"""从 ModelScope 下载 GGUF 并导入 Ollama。

不用 shell 生成 Modelfile —— bash 的 echo 会吞掉 <|im_start|> 这类尖括号标记,
导致 ollama create 报 "unexpected EOF"。这里全部用 Python 写文件。
"""
import os
import shutil
import sys
import json
import time
import urllib.request
import urllib.error

OLLAMA = r"C:\Users\mrhua\AppData\Local\Programs\Ollama\ollama.exe"
# 权重目录跟着脚本走：项目迁移后写死绝对路径会下载到不存在的旧目录
DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gguf_models")
os.makedirs(DL, exist_ok=True)

# 本地请求不走代理(沙箱/企业代理会劫持)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TEMPLATES = {
    "qwen3": (
        'TEMPLATE """{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n'
        '{{ end }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n'
        '<|im_start|>assistant\n{{ .Response }}<|im_end|>"""\n'
        'PARAMETER stop "<|im_start|>"\n'
        'PARAMETER stop "<|im_end|>"\n'
        'PARAMETER temperature 0.6\n'
        'PARAMETER top_p 0.95\n'
        'PARAMETER top_k 20\n'
    ),
    "gemma3": (
        'TEMPLATE """<bos><start_of_turn>user\n{{ .Prompt }}<end_of_turn>\n'
        '<start_of_turn>model\n{{ .Response }}<end_of_turn>"""\n'
        'PARAMETER stop "<end_of_turn>"\n'
        'PARAMETER stop "<eos>"\n'
        'PARAMETER temperature 1.0\n'
        'PARAMETER top_k 64\n'
    ),
    "llama3": (
        'TEMPLATE """<|start_header_id|>user<|end_header_id|>\n\n'
        '{{ .Prompt }}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n'
        '{{ .Response }}<|eot_id|>"""\n'
        'PARAMETER stop "<|eot_id|>"\n'
        'PARAMETER stop "<|end_of_text|>"\n'
        'PARAMETER temperature 0.6\n'
        'PARAMETER top_p 0.9\n'
    ),
    "chatml": (
        'TEMPLATE """{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n'
        '{{ end }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n'
        '<|im_start|>assistant\n{{ .Response }}<|im_end|>"""\n'
        'PARAMETER stop "<|im_start|>"\n'
        'PARAMETER stop "<|im_end|>"\n'
        'PARAMETER temperature 0.6\n'
        'PARAMETER top_p 0.95\n'
    ),
}

# (魔搭仓库, gguf文件名, Ollama标签, 模板)  —— 全部已通过 API 核实存在
MODELS = [
    ("Qwen/Qwen3-0.6B-GGUF",    "Qwen3-0.6B-Q8_0.gguf",        "qwen3:0.6b",        "qwen3"),
    ("Qwen/Qwen3-1.7B-GGUF",    "Qwen3-1.7B-Q8_0.gguf",        "qwen3:1.7b",        "qwen3"),
    ("unsloth/gemma-3-1b-it-GGUF", "gemma-3-1b-it-Q4_K_M.gguf", "gemma3:1b",         "gemma3"),
    ("unsloth/DeepSeek-R1-Distill-Qwen-1.5B-GGUF",
     "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",                   "deepseek-r1:1.5b",  "chatml"),
    ("unsloth/Llama-3.2-1B-Instruct-GGUF",
     "Llama-3.2-1B-Instruct-Q4_K_M.gguf",                           "llama3.2:1b",       "llama3"),
    ("Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
     "qwen2.5-coder-3b-instruct-q4_k_m.gguf",                       "qwen2.5-coder:3b",  "chatml"),
    ("unsloth/Llama-3.2-3B-Instruct-GGUF",
     "Llama-3.2-3B-Instruct-Q4_K_M.gguf",                           "llama3.2:3b",       "llama3"),
    ("Qwen/Qwen3-4B-GGUF",      "Qwen3-4B-Q4_K_M.gguf",        "qwen3:4b",          "qwen3"),
    ("unsloth/gemma-3-4b-it-GGUF", "gemma-3-4b-it-Q4_K_M.gguf", "gemma3:4b",         "gemma3"),
    ("Qwen/Qwen3-8B-GGUF",      "Qwen3-8B-Q4_K_M.gguf",        "qwen3:8b",          "qwen3"),
]


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


def download(repo, fname):
    out = os.path.join(DL, fname)
    if valid_gguf(out):
        print("[CACHED] %s  %s" % (human(os.path.getsize(out)), fname), flush=True)
        return out
    url = ("https://modelscope.cn/api/v1/models/%s/repo"
           "?Revision=master&FilePath=%s" % (repo, fname))
    print("      下载中: %s/%s" % (repo, fname), flush=True)
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
                    pct = got * 100.0 / total
                    spd = got / max(1e-6, time.time() - t0) / 1024 / 1024
                    sys.stdout.write("\r      %.1f%%  %s / %s  (%.1f MB/s)"
                                     % (pct, human(got), human(total), spd))
                    sys.stdout.flush()
        print()
    except Exception as e:
        print("      [下载异常] %r" % (e,), flush=True)
        if os.path.exists(out):
            os.remove(out)
        return None
    if not valid_gguf(out):
        print("      [无效] GGUF 头校验失败", flush=True)
        os.remove(out)
        return None
    print("      [DL-OK] %s" % human(os.path.getsize(out)), flush=True)
    return out


def main():
    import subprocess
    ok, fail = [], []
    for repo, fname, tag, tpl in MODELS:
        print("=" * 50, flush=True)
        print("[MODEL] %s   (魔搭 %s)" % (tag, repo), flush=True)
        gguf = download(repo, fname)
        if not gguf:
            fail.append(tag)
            continue
        mf = os.path.join(DL, "Modelfile." + tag.replace(":", "_"))
        with open(mf, "w", encoding="utf-8", newline="\n") as f:
            f.write("FROM " + gguf.replace("\\", "/") + "\n")
            f.write(TEMPLATES[tpl])
        r = subprocess.run([OLLAMA, "create", tag, "-f", mf],
                           capture_output=True, text=True, timeout=1800)
        listing = subprocess.run([OLLAMA, "list"], capture_output=True, text=True)
        if tag in listing.stdout:
            print("[OK] 导入成功: %s" % tag, flush=True)
            ok.append(tag)
        else:
            print("[FAIL] create 失败: %s\n%s\n%s"
                  % (tag, r.stdout[-500:], r.stderr[-500:]), flush=True)
            fail.append(tag)

    print("=" * 50, flush=True)
    print("成功 %d / 失败 %d" % (len(ok), len(fail)), flush=True)
    print("成功: %s" % ", ".join(ok), flush=True)
    if fail:
        print("失败: %s" % ", ".join(fail), flush=True)
    print("-" * 50, flush=True)
    print(subprocess.run([OLLAMA, "list"], capture_output=True, text=True).stdout, flush=True)


if __name__ == "__main__":
    main()