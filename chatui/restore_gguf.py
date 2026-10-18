"""从 Ollama 的 blob 还原 GGUF 权重（打包用）。

背景：源码目录里的 gguf_models 已被清理，但 Ollama 安装模型时把同一份 GGUF
存进了 %USERPROFILE%\\.ollama\\models\\blobs\\sha256-*。`ollama show --modelfile`
能拿到 FROM 路径，拷回来即可，无需重新下载 19GB。
"""
import os
import shutil
import subprocess
import sys

OLLAMA = r"C:\Users\mrhua\AppData\Local\Programs\Ollama\ollama.exe"
OUT = r"E:\Workbuddy\离线AI\offline-ai\chatui\gguf_models"

# 分发清单里的 10 个模型（与 bundle_manifest.json 对应）
MODELS = {
    "qwen3:0.6b": "Qwen3-0.6B-Q8_0.gguf",
    "gemma3:1b": "gemma-3-1b-it-Q4_K_M.gguf",
    "llama3.2:1b": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    "deepseek-r1:1.5b": "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
    "qwen3:1.7b": "Qwen3-1.7B-Q8_0.gguf",
    "llama3.2:3b": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    "qwen2.5-coder:3b": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
    "qwen3:4b": "Qwen3-4B-Q4_K_M.gguf",
    "gemma3:4b": "gemma-3-4b-it-Q4_K_M.gguf",
}
VISION = "gemma3:4b-vision"
MMPROJ = "gemma-3-4b-mmproj-F16.gguf"


def from_paths(model):
    r = subprocess.run([OLLAMA, "show", model, "--modelfile"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.upper().startswith("FROM "):
            out.append(line[5:].strip().strip('"'))
    return out


def copy_gguf(src, dst):
    if not os.path.isfile(src):
        return False, "源不存在"
    with open(src, "rb") as f:
        magic = f.read(4)
    if magic != b"GGUF":
        return False, "不是 GGUF（magic=%r）" % magic
    size = os.path.getsize(src)
    if os.path.isfile(dst) and os.path.getsize(dst) == size:
        return True, "已存在（%.2f GB）" % (size / 1e9)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return True, "已拷贝 %.2f GB" % (size / 1e9)


def main():
    os.makedirs(OUT, exist_ok=True)
    ok = fail = 0
    for model, name in MODELS.items():
        paths = from_paths(model)
        if not paths:
            print("[FAIL] %-22s 取不到 FROM" % model)
            fail += 1
            continue
        good, msg = copy_gguf(paths[0], os.path.join(OUT, name))
        print("[%s] %-22s %s" % ("OK" if good else "FAIL", model, msg))
        ok += 1 if good else 0
        fail += 0 if good else 1

    # 视觉模型：FROM 权重 + FROM mmproj（两条）
    paths = from_paths(VISION)
    if len(paths) >= 2:
        good, msg = copy_gguf(paths[0], os.path.join(OUT, MODELS["gemma3:4b"]))
        print("[%s] %-22s 权重 %s" % ("OK" if good else "FAIL", VISION, msg))
        good2, msg2 = copy_gguf(paths[1], os.path.join(OUT, MMPROJ))
        print("[%s] %-22s mmproj %s" % ("OK" if good2 else "FAIL", VISION, msg2))
        ok += sum([good, good2])
        fail += sum([not good, not good2])
    else:
        print("[FAIL] 视觉模型只找到 %d 条 FROM" % len(paths))
        fail += 1

    print("OK %d / FAIL %d" % (ok, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
