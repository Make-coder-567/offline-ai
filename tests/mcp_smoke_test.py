"""MCP 服务冒烟：以 stdio 启动 mcp_server.py，走完成 initialize → tools/list → 一次真实调用。

MCP 的 stdio 传输是"每行一个 JSON-RPC"，所以直接按行读写即可。
"""
import json
import os
import subprocess
import sys
import time

ROOT = r"E:\Workbuddy\离线AI\offline-ai"
PY = sys.executable


def main():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen([PY, os.path.join(ROOT, "server", "mcp_server.py")],
                         cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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

        # 不需要推理的轻量调用
        if "local_model_list" in names:
            r = call("tools/call", {"name": "local_model_list", "arguments": {}})
            c = (r.get("result") or {}).get("content") or []
            text = c[0].get("text", "") if c else ""
            check("调用 model_list", bool(text) and "error" not in text.lower()[:40],
                  text[:120])

        # 需要推理的一次调用（短问句，验证模型通路）
        if "local_llm_chat" in names:
            r = call("tools/call", {"name": "local_llm_chat",
                                    "arguments": {"prompt": "/no_think 只回答两个字：你好"}},
                     _id=99)
            c = (r.get("result") or {}).get("content") or []
            text = c[0].get("text", "") if c else ""
            check("调用 chat（真实推理）", bool(text.strip()), text[:120])
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
