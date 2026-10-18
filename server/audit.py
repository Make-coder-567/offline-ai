"""操作审计：记录智能体对工作区的"改变型"动作，并支持一键回滚。

落盘：
  <workspace>/.offline-audit/audit.jsonl          每条一行，追加写 + flush
  <workspace>/.offline-audit/snapshots/<id>.bak   写文件前的原内容快照

设计取舍：
- 只记会改变世界的动作（write_file / run_python / rollback），只读动作不记，
  否则几百次 list_dir 就能把日志撑爆；
- 覆盖写之前先快照原文件（不存在则无快照，回滚时按"删除恢复"处理）；
- 追加写用 open(..., "a") + flush，进程被强杀也只丢最后半行。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_LOG_BYTES = 2 * 1024 * 1024      # 超过则从头截断一半，防无限增长


class Audit:
    def __init__(self, workspace_root: str | Path):
        self.root = Path(workspace_root).resolve()
        self.dir = self.root / ".offline-audit"
        self.snaps = self.dir / "snapshots"
        self.path = self.dir / "audit.jsonl"

    # ---------- 快照 ----------
    def snapshot(self, p: Path) -> str:
        """复制待覆盖文件的当前内容，返回快照文件名；原文件不存在返回 ""。"""
        try:
            p = Path(p)
            if not p.is_file():
                return ""
            self.snaps.mkdir(parents=True, exist_ok=True)
            name = "%d_%s.bak" % (int(time.time() * 1000), _safe_name(p.name))
            (self.snaps / name).write_bytes(p.read_bytes())
            return name
        except Exception:
            return ""

    # ---------- 记录 ----------
    def log(self, action: str, path: str = "", detail: str = "", snap: str = "",
            ok: bool = True, task_id: str = "") -> dict:
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts_ms": int(time.time() * 1000),
            "action": action,
            "path": str(path),
            "snap": snap,
            "ok": bool(ok),
            "task": task_id,
            "detail": (detail or "")[:400],
        }
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
            self._trim()
        except Exception:
            pass
        return entry

    def _trim(self):
        try:
            if not self.path.is_file() or self.path.stat().st_size <= MAX_LOG_BYTES:
                return
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            keep = lines[len(lines) // 2:]
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(keep) + "\n")
            import os
            os.replace(tmp, self.path)
        except Exception:
            pass

    def tail(self, n: int = 20) -> list:
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []
        out = []
        for ln in lines[-max(1, int(n)):]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    # ---------- 回滚 ----------
    def rollback(self, entry=None) -> str:
        """回滚一条记录；entry 省略时回滚最近一条"可回滚"的记录。"""
        entries = self.tail(200)
        if not entries:
            return "[error] 没有可回滚的操作记录"
        if entry is None:
            cand = [e for e in reversed(entries)
                    if e.get("action") in ("write_file",) and e.get("path")]
            if not cand:
                return "[error] 最近的记录不可回滚（只读操作或缺少快照）"
            entry = cand[0]
        rel = entry.get("path") or ""
        target = (self.root / rel).resolve()
        # 用 is_relative_to 做路径分量级校验：startswith 会被 "<ws>-evil" 这类
        # 同级前缀目录绕过（与 Tools._safe 同一个坑，2026-10-17 已修过一次）
        if not target.is_relative_to(self.root):
            return f"[error] 拒绝回滚到工作区之外：{rel}"
        snap = entry.get("snap") or ""
        try:
            if snap:
                src = self.snaps / snap
                if not src.is_file():
                    return f"[error] 快照缺失：{snap}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(src.read_bytes())
                msg = f"[ok] 已回滚 {rel}（来自快照 {snap}）"
            else:
                # 原本不存在的文件：回滚 = 删除新建的文件
                if target.is_file():
                    target.unlink()
                    msg = f"[ok] 已删除新建文件 {rel}（回滚到「不存在」状态）"
                else:
                    return f"[error] 无可回滚内容：{rel}"
            self.log("rollback", rel, msg, task_id=entry.get("task", ""))
            return msg
        except Exception as e:
            return f"[error] 回滚失败：{e}"


def _safe_name(name: str) -> str:
    keep = [c for c in name if c.isalnum() or c in "._-"]
    return ("".join(keep) or "file")[:48]
