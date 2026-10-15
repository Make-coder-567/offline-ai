"""导入完成后, 实测每个模型能否正常对话(而不是只看 ollama list)。

手写 Modelfile 的 TEMPLATE 可能出错, 所以必须真跑一轮 /api/chat 验证:
  - 响应非空
  - 没有把 <|im_start|> / <start_of_turn> 之类的模板标记漏出来
  - 计时(CPU 机器上大模型的 tok/s 参考)

用法:
  python verify_models.py            # 验证全部已知模型
  python verify_models.py qwen3:0.6b # 只验证某一个
"""
import json
import sys
import time
import urllib.request

OLLAMA_BASE = "http://127.0.0.1:11434"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 已知要验证的模型(含最初就有的 qwen2.5:7b)
MODELS = [
    "qwen2.5:7b",
    "qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b",
    "gemma3:1b", "gemma3:4b",
    "llama3.2:1b", "llama3.2:3b",
    "deepseek-r1:1.5b", "qwen2.5-coder:3b",
]

PROMPT = "请用中文用一句话介绍你自己, 并说明你的参数量。"


def chat(model, prompt):
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.0},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_BASE + "/api/chat", data=data,
        headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=180) as r:
        return json.loads(r.read().decode())


def verify(model):
    try:
        t0 = time.time()
        resp = chat(model, PROMPT)
        msg = resp.get("message", {}).get("content", "")
        dt = time.time() - t0
        nonempty = len(msg.strip()) > 0
        leak = ("<|im_start|>" in msg or "<|im_end|>" in msg
                or "<start_of_turn>" in msg or "<end_of_turn>" in msg
                or "<|start_header_id|>" in msg or "<|eot_id|>" in msg)
        status = "OK" if (nonempty and not leak) else "BAD"
        print("[%s] %-18s %6.1fs  %4d chars  leak=%s" %
              (status, model, dt, len(msg), leak))
        print("      >> " + msg[:160].replace("\n", " "))
        return nonempty and not leak
    except Exception as e:
        print("[ERR] %-18s %r" % (model, e))
        return False


if __name__ == "__main__":
    targets = sys.argv[1:] or MODELS
    results = {}
    for m in targets:
        results[m] = verify(m)
    print("=" * 50)
    print("PASS(%d): %s" % (sum(results.values()),
                            ", ".join(m for m, v in results.items() if v)))
    print("FAIL(%d): %s" % (len(results) - sum(results.values()),
                            ", ".join(m for m, v in results.items() if not v)))
