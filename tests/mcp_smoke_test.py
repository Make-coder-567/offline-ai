"""MCP 服务冒烟：以 stdio 启动 mcp_server.py，走完成 initialize → tools/list → 一次真实调用。

MCP 的 stdio 传输是"每行一个 JSON-RPC"，所以直接按行读写即可。
"""
import json
import os
import subprocess
import sys
import time

from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])  # 2026-11-16：去掉硬编码绝对路径
PY = sys.executable

# 可选参数：检查任意一份 mcp_server.py（例如打包后的 _internal/server/）
SERVER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "server", "mcp_server.py")
CWD = sys.argv[2] if len(sys.argv) > 2 else ROOT


def main():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen([PY, SERVER],
                         cwd=CWD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env)
    n = [0]

    def send(obj):
        p.stdin.write((json.dumps(obj) + "\n").encode())
        p.stdin.flush()

    def recv(timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = p.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except Exception:
                continue
        raise TimeoutError("no response")

    def call(method, params=None, _id=None):
        n[0] += 1
        send({"jsonrpc": "2.0", "id": _id or n[0], "method": method,
              "params": params or {}})
        return recv()

    ok, fail = 0, 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print("[OK]  ", name)
        else:
            fail += 1
            print("[FAIL]", name, detail)

    try:
        r = call("initialize", {"protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "smoke", "version": "1"}})
        check("initialize", "result" in r, str(r)[:200])
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        r = call("tools/list")
        tools = (r.get("result") or {}).get("tools") or []
        names = [t["name"] for t in tools]
        check("tools/list 非空", len(tools) >= 5, str(names))
        print("      工具:", ", ".join(names))

        # 2026-11-16：核心工具缺失应算 FAIL，不再静默跳过（否则删工具反而更绿）
        for required in ("local_model_list", "local_audit", "local_llm_chat"):
            check(f"工具存在: {required}", required in names, str(names))

        # 不需要推理的轻量调用
        if "local_model_list" in names:
            r = call("tools/call", {"name": "local_model_list", "arguments": {}})
            c = (r.get("result") or {}).get("content") or []
            text = c[0].get("text", "") if c else ""
            check("调用 model_list", bool(text) and "error" not in text.lower()[:40],
                  text[:120])

        # 工作区相关：audit 需要按 config 解析 workspace_root
        # 2026-11-16：修正参数名 limit->n（真实签名 local_audit(n: int = 20)）
        if "local_audit" in names:
            r = call("tools/call", {"name": "local_audit", "arguments": {"n": 3}},
                     _id=98)
            c = (r.get("result") or {}).get("content") or []
            text = c[0].get("text", "") if c else ""
            check("调用 local_audit（工作区解析）",
                  bool(text) and "error" not in text[:40].lower(), text[:120])

        # 需要推理的一次调用（短问句，验证模型通路）
        # 2026-11-16：chat 必须排除 [error]，Ollama 不可用时不再"非空即过"
        if "local_llm_chat" in names:
            r = call("tools/call", {"name": "local_llm_chat",
                                    "arguments": {"prompt": "/no_think 只回答两个字：你好"}},
                     _id=99)
            c = (r.get("result") or {}).get("content") or []
            text = c[0].get("text", "") if c else ""
            is_error = text.strip().lower().startswith("[error]") or "ollama" in text[:60].lower()
            check("调用 chat（真实推理）",
                  bool(text.strip()) and not is_error, text[:120])
    finally:
        try:
            p.stdin.close()
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            pass
        err = p.stderr.read().decode("utf-8", "replace")[:400] if p.stderr else ""
        if err.strip():
            print("stderr:", err.strip()[:300])

    print("OK %d / FAIL %d" % (ok, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
