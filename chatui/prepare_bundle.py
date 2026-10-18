# -*- coding: utf-8 -*-
"""
准备离线打包所需的模型资源：
1) 校验 chatui/gguf_models 下的 GGUF 与当前 Ollama 模型库中的 blob 完全一致(sha256)。
2) 为每个模型生成 Modelfile(从 `ollama show --modelfile` 提取真实模板, 仅替换 FROM 指向本地 GGUF)。
3) 写出 bundle_manifest.json 供 offline_chat.spec 读取, 把 GGUF 打进 exe。
校验通过才会写 Modelfile/清单, 否则报错退出(避免把损坏模型发给别人)。

支持多文件模型（视觉模型 = 文本 GGUF + mmproj 投影），见 EXTRA_MODELS。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

CHATUI = os.path.dirname(os.path.abspath(__file__))
GGUF_DIR = os.path.join(CHATUI, "gguf_models")
BUNDLE_DIR = os.path.join(CHATUI, "bundle_models")
OL = shutil.which("ollama") or r"C:\Users\mrhua\AppData\Local\Programs\Ollama\ollama.exe"

# 单 GGUF 模型(<=4B 实用集, 共 9 个)。键=模型名, 值=gguf_models 中的文件名。
MODELS = {
    "qwen3:0.6b":        "Qwen3-0.6B-Q8_0.gguf",
    "gemma3:1b":         "gemma-3-1b-it-Q4_K_M.gguf",
    "llama3.2:1b":       "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    "deepseek-r1:1.5b":  "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
    "qwen3:1.7b":        "Qwen3-1.7B-Q8_0.gguf",
    "llama3.2:3b":       "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    "qwen2.5-coder:3b":  "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
    "qwen3:4b":          "Qwen3-4B-Q4_K_M.gguf",
    "gemma3:4b":         "gemma-3-4b-it-Q4_K_M.gguf",
}

# 多文件模型（视觉）：文本权重 + 视觉投影，顺序必须与 `ollama show` 中 FROM 顺序一致
EXTRA_MODELS = {
    "gemma3:4b-vision": [
        "gemma-3-4b-it-Q4_K_M.gguf",
        "gemma-3-4b-mmproj-F16.gguf",
    ],
}


def sha256_of(path, progress_name=None):
    h = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress_name and done % (256 << 20) < (8 << 20):
                sys.stderr.write("\r  %s %.1f/%.1f GB" % (progress_name, done / 1e9, total / 1e9))
                sys.stderr.flush()
    if progress_name:
        sys.stderr.write("\n")
    return h.hexdigest()


def expected_digests(model):
    """返回该模型 Modelfile 中所有 FROM 的 blob sha256（视觉模型为 [文本, mmproj]）。"""
    out = subprocess.run([OL, "show", model, "--modelfile"],
                         capture_output=True, text=True).stdout
    digests = []
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("FROM "):
            digests.append(s.split("sha256-")[-1].strip())
    if not digests:
        raise RuntimeError("无法从 ollama show 获取 %s 的 blob digest" % model)
    return digests


def main():
    os.makedirs(BUNDLE_DIR, exist_ok=True)
    specs = [(m, [g]) for m, g in MODELS.items()]
    specs += [(m, files) for m, files in EXTRA_MODELS.items()]
    manifest = []
    print("开始校验 %d 个模型（共 %d 个 GGUF 文件）..."
          % (len(specs), sum(len(f) for _, f in specs)))
    for model, files in specs:
        paths = [os.path.join(GGUF_DIR, f) for f in files]
        for p in paths:
            if not os.path.isfile(p):
                sys.exit("缺少 GGUF 文件: %s" % p)
        expects = expected_digests(model)
        if len(expects) != len(paths):
            sys.exit("%s 的 FROM 数量(%d)与本地文件数(%d)不一致，请检查 EXTRA_MODELS 配置"
                     % (model, len(expects), len(paths)))
        print("[校验] %-20s 期望 %d 个 blob" % (model, len(expects)))
        for p, want, name in zip(paths, expects, files):
            actual = sha256_of(p, model)
            if actual != want:
                sys.exit("校验失败: %s 的 %s sha256 不匹配!\n  期望=%s\n  实际=%s\n"
                         "该文件可能损坏, 请重新从 ModelScope 下载后再打包。"
                         % (model, name, want, actual))
            print("        OK  sha256 一致 (%s)" % name)

        # 提取真实 Modelfile 模板，按顺序把每个 FROM 替换为本地文件
        out = subprocess.run([OL, "show", model, "--modelfile"],
                             capture_output=True, text=True).stdout
        lines = []
        idx = 0
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("#"):           # 丢弃注释行
                continue
            if s.startswith("FROM "):       # 用本地 GGUF / mmproj 替换
                lines.append("FROM ./" + files[idx])
                idx += 1
                continue
            lines.append(line.rstrip())
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        safe = model.replace(":", "-")
        mdir = os.path.join(BUNDLE_DIR, safe)
        os.makedirs(mdir, exist_ok=True)
        with open(os.path.join(mdir, "Modelfile"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        entry = {
            "model": model,
            "safe": safe,
            "gguf_name": files[0],
            "gguf_abs": paths[0],
        }
        if len(files) > 1:
            entry["mmproj_name"] = files[1]
            entry["mmproj_abs"] = paths[1]
        manifest.append(entry)
        print("        已生成 Modelfile -> bundle_models/%s/Modelfile" % safe)

    with open(os.path.join(CHATUI, "bundle_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    total_gb = sum(os.path.getsize(p) for m in manifest
                   for p in [m["gguf_abs"]] + ([m["mmproj_abs"]] if m.get("mmproj_abs") else [])) / 1e9
    print("\n完成! 共 %d 个模型, 文件合计 %.1f GB。清单已写入 bundle_manifest.json。"
          % (len(manifest), total_gb))


if __name__ == "__main__":
    main()
