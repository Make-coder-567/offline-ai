"""离线AI 智能体（独立运行）：

监听 workspace 内 .offline-bridge/inbox 的任务 JSON，
调用本机 Ollama 模型 + 工具自主处理，结果写回 outbox，并从 inbox 移除。

启动: python agent.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))

import yaml
from bridge.protocol import Bridge, Task
from tools import Tools

try:
    import ollama
except ImportError:
    ollama = None


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def run_llm(cfg: dict, prompt: str, temperature: float = 0.3) -> str:
    if ollama is None:
        return "[error] ollama python lib not installed"
    try:
        r = ollama.generate(
            model=cfg["model"], prompt=prompt, options={"temperature": temperature}
        )
        return r["response"]
    except Exception as e:
        return f"[error] ollama: {e}"


def handle_task(cfg: dict, bridge: Bridge, task: Task, tools: Tools) -> str:
    ctx = ""
    for f in task.files:
        ctx += f"\n--- file: {f} ---\n" + tools.read_file(f) + "\n"
    system = (
        "你是运行在本地的离线AI助手，可读取用户工作区文件。"
        "根据任务直接给出结论或生成内容；若需产出文件，请在回复中说明将写入的路径。"
    )
    prompt = f"{system}\n\n任务: {task.prompt}\n{ctx}\n\n请直接给出结果："
    return run_llm(cfg, prompt)


def main() -> None:
    cfg = load_config()
    bridge = Bridge(cfg["workspace_root"], cfg["bridge_dir"])
    tools = Tools(
        cfg["workspace_root"],
        allow_shell=cfg.get("allow_shell", False),
        shell_allowlist=cfg.get("shell_allowlist"),
    )
    print(f"[offline-agent] started model={cfg['model']} bridge={bridge.root}", flush=True)
    while True:
        for path, task in bridge.list_pending():
            bridge.mark_running(path, task)
            bridge.log(f"start {task.task_id}: {task.prompt[:50]}")
            try:
                task.result = handle_task(cfg, bridge, task, tools)
                task.status = "done"
            except Exception as e:
                task.result = f"[error] {e}"
                task.status = "error"
            bridge.write_outbox(task)
            bridge.log(f"done {task.task_id} -> {task.status}")
            try:
                path.unlink()
            except Exception:
                pass
        time.sleep(cfg.get("poll_interval", 3))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[offline-agent] stopped", flush=True)
