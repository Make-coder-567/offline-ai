"""Ollama 调用前的内存卫生：把非目标模型卸掉。

背景（实测）：Ollama 默认 `keep_alive` 会把用过的每个模型都留在内存里，
16GB 机器上切两三次模型就吃满可用内存，KV cache 分配失败后推理会退化成
"0000…"/"BotBot…" 这类重复字符流。UI 侧已在切模型时卸载，这里补上
MCP / agent / gateway 三条无界面通路。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

# 每个 base 的上次清理时间，避免每次请求都打一轮 /api/ps
_LAST = {}
_LOCK = threading.Lock()
_THROTTLE = 60.0


def _opener():
    # 本地请求不走代理（企业代理会劫持 127.0.0.1）
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def unload_others(base: str, keep: str, timeout: float = 3.0) -> list:
    """卸载除 keep 之外的已加载模型；返回被卸载的模型名。失败静默。"""
    now = time.time()
    with _LOCK:
        if now - _LAST.get(base, 0) < _THROTTLE:
            return []
        _LAST[base] = now
    dropped = []
    op = _opener()
    try:
        with op.open(base.rstrip("/") + "/api/ps", timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return dropped
    for m in data.get("models") or []:
        name = (m.get("name") or "").strip()
        if not name or name == keep:
            continue
        try:
            body = json.dumps({"model": name, "prompt": "",
                               "keep_alive": 0}).encode()
            req = urllib.request.Request(
                base.rstrip("/") + "/api/generate", data=body,
                headers={"Content-Type": "application/json"})
            with op.open(req, timeout=15) as resp:
                resp.read()
            dropped.append(name)
        except Exception:
            continue
    return dropped


def unload_others_async(base: str, keep: str):
    """请求前调用：后台线程清理，不阻塞本次生成。"""
    threading.Thread(target=unload_others, args=(base, keep), daemon=True).start()
