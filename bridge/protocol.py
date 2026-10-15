"""离线AI <-> WorkBuddy 文件桥协议。

约定目录（相对 workspace_root）：
  .offline-bridge/inbox     WorkBuddy/用户放入任务 JSON
  .offline-bridge/outbox    离线AI 写回结果
  .offline-bridge/context   共享上下文 / 项目摘要
  .offline-bridge/log       双向操作日志

任务 JSON schema:
{
  "task_id": "t1",
  "prompt":  "要离线AI做的事",
  "files":   ["相对 workspace 的路径"],
  "status":  "pending|running|done|error",
  "result":  null
}
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Task:
    task_id: str
    prompt: str
    files: list[str] = field(default_factory=list)
    status: str = "pending"
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            task_id=d["task_id"],
            prompt=d["prompt"],
            files=d.get("files", []),
            status=d.get("status", "pending"),
            result=d.get("result"),
            created_at=d.get("created_at", time.time()),
        )


class Bridge:
    def __init__(self, workspace_root: str, bridge_dir: str):
        self.root = Path(workspace_root) / bridge_dir
        for sub in ("inbox", "outbox", "context", "log"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def inbox_path(self) -> Path:
        return self.root / "inbox"

    def outbox_path(self) -> Path:
        return self.root / "outbox"

    def context_path(self) -> Path:
        return self.root / "context"

    def log_path(self) -> Path:
        return self.root / "log"

    def list_pending(self):
        """返回 [(path, Task), ...]，仅 pending 状态。"""
        tasks = []
        for f in sorted(self.inbox_path().glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("status", "pending") == "pending":
                    tasks.append((f, Task.from_dict(d)))
            except Exception:
                continue
        return tasks

    def mark_running(self, path: Path, task: Task) -> None:
        task.status = "running"
        path.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def write_outbox(self, task: Task) -> Path:
        out = self.outbox_path() / f"{task.task_id}.json"
        out.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    def log(self, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        (self.log_path() / "bridge.log").open("a", encoding="utf-8").write(f"[{ts}] {msg}\n")
