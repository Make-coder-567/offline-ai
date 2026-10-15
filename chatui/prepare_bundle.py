# -*- coding: utf-8 -*-
"""
准备离线打包所需的模型资源：
1) 校验 chatui/gguf_models 下的 GGUF 与当前 Ollama 模型库中的 blob 完全一致(sha256)。
2) 为每个模型生成 Modelfile(从 `ollama show --modelfile` 提取真实模板, 仅替换 FROM 指向本地 GGUF)。
3) 写出 bundle_manifest.json 供 offline_chat.spec 读取, 把 GGUF 打进 exe。
校验通过才会写 Modelfile/清单, 否则报错退出(避免把损坏模型发给别人)。
"""
import os, sys, json, hashlib, subprocess

CHATUI = os.path.dirname(os.path.abspath(__file__))
GGUF_DIR = os.path.join(CHATUI, "gguf_models")
BUNDLE_DIR = os.path.join(CHATUI, "bundle_models")
OL = r"C:\Users\mrhua\AppData\Local\Programs\Ollama\ollama.exe"

# 要打包的模型(<=4B 实用集, 共 9 个)。键=模型名, 值=gguf_models 中的文件名。
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
                sys.stderr.write("\r  %s %.1f/%.1f GB" % (progress_name, done/1e9, total/1e9))
                sys.stderr.flush()
    if progress_name:
        sys.stderr.write("\n")
    return h.hexdigest()


def expected_digest(model):
    out = subprocess.run([OL, "show", model, "--modelfile"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("FROM "):
            # FROM C:\...\blobs\sha256-<digest>
            digest = line.split("sha256-")[-1].strip()
            return digest
    raise RuntimeError("无法从 ollama show 获取 %s 的 blob digest" % model)


def main():
    os.makedirs(BUNDLE_DIR, exist_ok=True)
    manifest = []
    print("开始校验 %d 个模型的 GGUF 完整性..." % len(MODELS))
    for model, gguf_name in MODELS.items():
        gguf_path = os.path.join(GGUF_DIR, gguf_name)
        if not os.path.isfile(gguf_path):
            sys.exit("缺少 GGUF 文件: %s" % gguf_path)
        expect = expected_digest(model)
        print("[校验] %-18s 期望 sha256=%s" % (model, expect[:16]))
        actual = sha256_of(gguf_path, model)
        if actual != expect:
            sys.exit("校验失败: %s 的 GGUF(%s) sha256 不匹配!\n  期望=%s\n  实际=%s\n"
                     "该文件可能损坏, 请重新从 ModelScope 下载后再打包。" % (model, gguf_name, expect, actual))
        print("        OK  sha256 一致 (%s)" % gguf_name)

        # 提取真实 Modelfile 模板
        out = subprocess.run([OL, "show", model, "--modelfile"],
                             capture_output=True, text=True).stdout
        lines = []
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("#"):           # 丢弃注释行
                continue
            if s.startswith("FROM "):       # 用本地 GGUF 替换
                lines.append("FROM ./" + gguf_name)
                continue
            lines.append(line.rstrip())
        # 去掉首尾空行
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        safe = model.replace(":", "-")
        mdir = os.path.join(BUNDLE_DIR, safe)
        os.makedirs(mdir, exist_ok=True)
        with open(os.path.join(mdir, "Modelfile"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        manifest.append({
            "model": model,
            "safe": safe,
            "gguf_name": gguf_name,
            "gguf_abs": gguf_path,
        })
        print("        已生成 Modelfile -> bundle_models/%s/Modelfile" % safe)

    with open(os.path.join(CHATUI, "bundle_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    total_gb = sum(os.path.getsize(m["gguf_abs"]) for m in manifest) / 1e9
    print("\n完成! 共 %d 个模型, GGUF 合计 %.1f GB。清单已写入 bundle_manifest.json。"
          % (len(manifest), total_gb))


if __name__ == "__main__":
    main()
