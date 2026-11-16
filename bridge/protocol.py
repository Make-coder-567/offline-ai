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
  "status":  "pending|running|done|error|cancelled",
  "result":  null,
  "model":   null,        # 可选：显式指定模型
  "task_type": null       # 可选：code/fast/reason/vision，按 config.routes 选模型
}

取消任务：在 inbox 放置空文件 `<task_id>.cancel`，agent 会在下一步前中止。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import re

# 合法 task_id：仅字母/数字/下划线/短横线/点，且不得以点开头、不得含路径分隔符。
# 2026-11-16 加固：task_id 直接来自 inbox JSON，会被拼进 outbox 文件名与
# cancel 标记路径，历史上未校验导致 "../evil" 越界读写/删除。
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def safe_task_id(task_id: str) -> str:
    """校验并规范化 task_id；非法值抛 ValueError。"""
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id 不能为空且必须是字符串")
    if not _TASK_ID_RE.match(task_id) or ".." in task_id:
        raise ValueError(f"非法 task_id（含路径分隔或越界序列）: {task_id!r}")
    return task_id


@dataclass
class Task:
    task_id: str
    prompt: str
    files: list[str] = field(default_factory=list)
    status: str = "pending"
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    model: Optional[str] = None          # 指定模型（覆盖 config 默认与路由）
    task_type: Optional[str] = None      # 任务类型（code/fast/reason/vision），走 config.routes

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            task_id=safe_task_id(d["task_id"]),
            prompt=d["prompt"],
            files=d.get("files", []),
            status=d.get("status", "pending"),
            result=d.get("result"),
            created_at=d.get("created_at", time.time()),
            model=d.get("model"),
            task_type=d.get("task_type"),
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
        """返回 [(path, Task), ...]，仅 pending 状态；重复 task_id 只取第一个。"""
        tasks = []
        seen = set()
        for f in sorted(self.inbox_path().glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("status", "pending") != "pending":
                    continue
                tid = d.get("task_id")
                if tid in seen:
                    self.log(f"[skip] 重复 task_id {tid}: {f.name}（已跳过）")
                    continue
                seen.add(tid)
                tasks.append((f, Task.from_dict(d)))
            except Exception as e:
                # 坏文件不再静默跳过：记日志，避免用户以为任务已提交
                self.log(f"[skip] 无法解析的任务文件 {f.name}: {e}")
                continue
        return tasks

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """先写临时文件再 rename（同分区原子操作），防止崩溃留下半个 JSON。"""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def mark_running(self, path: Path, task: Task) -> None:
        task.status = "running"
        self._atomic_write(path, json.dumps(task.to_dict(), ensure_ascii=False, indent=2))

    def write_outbox(self, task: Task) -> Path:
        tid = safe_task_id(task.task_id)  # 写入前兜底校验，防绕过 from_dict 直接构造 Task
        out = self.outbox_path() / f"{tid}.json"
        if out.exists():
            # 同 id 重复提交时加纳秒后缀，避免同一秒内覆盖上一次结果
            out = self.outbox_path() / f"{tid}-{time.time_ns()}.json"
        self._atomic_write(out, json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
        return out

    def reset_running(self) -> int:
        """agent 启动时调用：把 inbox 中滞留的 running 任务重置为 pending。

        agent 处理任务过程中崩溃（断电/强杀）会留下 running 死任务，
        而 list_pending 只收 pending，不重置则永不执行。返回重置条数。
        """
        n = 0
        for f in self.inbox_path().glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("status") == "running":
                    d["status"] = "pending"
                    self._atomic_write(f, json.dumps(d, ensure_ascii=False, indent=2))
                    n += 1
                    self.log(f"[recover] running -> pending: {f.name}")
            except Exception:
                continue
        return n

    def log(self, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        (self.log_path() / "bridge.log").open("a", encoding="utf-8").write(f"[{ts}] {msg}\n")
