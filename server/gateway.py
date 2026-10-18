"""OpenAI 兼容网关：把本机 Ollama 包装成 /v1/chat/completions，供第三方客户端直连。

只依赖标准库（http.server），不引入 fastapi/uvicorn。全程本地，不出网。

启动：
    python server/gateway.py [--port 8080]
然后任何支持 OpenAI 协议的客户端把 base_url 指到 http://127.0.0.1:8080/v1 即可。

已支持：
    GET  /v1/models
    POST /v1/chat/completions      （stream=false 返回 JSON；stream=true 走 SSE）
    GET  /health
未支持（返回 501）：/v1/embeddings、/v1/images 等。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma3:4b"
DEFAULT_PORT = 8080
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _ollama(path: str, payload=None, timeout: float = 600.0):
    url = OLLAMA + path
    if payload is None:
        with _OPENER.open(url, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _ollama_stream(path: str, payload):
    """逐行 yield Ollama 流式响应的 JSON 对象。"""
    req = urllib.request.Request(OLLAMA + path,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=600) as r:
        for line in r:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def list_models() -> list:
    try:
        tags = _ollama("/api/tags")
    except Exception:
        return []
    return [m.get("model") or m.get("name") for m in tags.get("models", [])]


class Handler(BaseHTTPRequestHandler):
    server_version = "offline-ai-gateway"
    model_default = DEFAULT_MODEL

    # ---------- 基础 ----------
    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, obj):
        payload = ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
        self.wfile.write(payload)
        self.wfile.flush()

    def log_message(self, fmt, *args):      # 安静一点：只记路径
        sys.stderr.write("[gateway] %s\n" % (fmt % args)[:200])

    # ---------- 路由 ----------
    def do_GET(self):
        if self.path.rstrip("/").endswith("/v1/models"):
            names = list_models() or [self.model_default]
            now = int(time.time())
            self._json(200, {"object": "list", "data": [
                {"id": n, "object": "model", "created": now, "owned_by": "offline-ai"}
                for n in names]})
        elif self.path.rstrip("/").endswith("/health"):
            try:
                list_models()
                self._json(200, {"ok": True, "ollama": OLLAMA})
            except Exception as e:
                self._json(503, {"ok": False, "error": str(e)})
        else:
            self._json(404, {"error": {"message": "not found: %s" % self.path}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/v1/chat/completions"):
            self._json(501, {"error": {"message": "仅支持 /v1/chat/completions"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._json(400, {"error": {"message": "invalid json: %s" % e}})
            return
        self._chat(req)

    # ---------- 会话补全 ----------
    def _chat(self, req: dict):
        model = req.get("model") or self.model_default
        messages = req.get("messages") or []
        if isinstance(req.get("prompt"), str) and not messages:
            messages = [{"role": "user", "content": req["prompt"]}]
        try:
            from server.ollama_guard import unload_others_async
            unload_others_async(self.base if hasattr(self, "base")
                                else "http://127.0.0.1:11434", model)
        except Exception:
            pass
        options = {"temperature": req.get("temperature", 0.3),
                   "num_ctx": int(req.get("num_ctx", 6144)),
                   "keep_alive": "10m"}
        if req.get("max_tokens"):
            options["num_predict"] = int(req["max_tokens"])
        payload = {"model": model, "messages": messages, "options": options,
                   "stream": bool(req.get("stream"))}

        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            cid = "chatcmpl-%d" % int(time.time())
            try:
                for obj in _ollama_stream("/api/chat", payload):
                    msg = obj.get("message") or {}
                    tok = msg.get("content") or ""
                    if tok:
                        self._sse({"id": cid, "object": "chat.completion.chunk",
                                   "created": int(time.time()), "model": model,
                                   "choices": [{"index": 0, "delta": {"content": tok},
                                                "finish_reason": None}]})
                self._sse({"id": cid, "object": "chat.completion.chunk",
                           "created": int(time.time()), "model": model,
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception as e:
                self._sse({"error": {"message": str(e)}})
            return

        try:
            r = _ollama("/api/chat", payload)
        except urllib.error.HTTPError as e:
            self._json(502, {"error": {"message": "ollama %s: %s" % (e.code, e.reason)}})
            return
        except Exception as e:
            self._json(502, {"error": {"message": str(e)}})
            return
        msg = r.get("message") or {}
        text = msg.get("content") or msg.get("thinking") or ""
        self._json(200, {
            "id": "chatcmpl-%d" % int(time.time()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": r.get("prompt_eval_count", 0),
                      "completion_tokens": r.get("eval_count", 0),
                      "total_tokens": (r.get("prompt_eval_count", 0)
                                       + r.get("eval_count", 0))},
        })


def main():
    global OLLAMA
    ap = argparse.ArgumentParser(description="离线AI · OpenAI 兼容网关")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ollama", default=OLLAMA)
    args = ap.parse_args()

    OLLAMA = args.ollama.replace("localhost", "127.0.0.1")
    Handler.model_default = args.model
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("[gateway] http://%s:%d/v1  ->  %s  (默认模型 %s)"
          % (args.host, args.port, OLLAMA, args.model), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway] stopped", flush=True)


if __name__ == "__main__":
    main()
