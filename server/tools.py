"""离线AI 工具集：受限的文件 / 工作区访问。

所有路径被限制在 workspace_root 内（防越权）。
shell 执行默认禁用，开启后仅允许白名单命令、且 cwd 限定在 workspace。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


class Tools:
    def __init__(
        self,
        workspace_root: str,
        allow_shell: bool = False,
        shell_allowlist: Optional[List[str]] = None,
    ):
        self.ws = Path(workspace_root).resolve()
        self.allow_shell = allow_shell
        self.shell_allowlist = set(shell_allowlist or ["dir", "ls", "cat", "python", "python3"])

    def _safe(self, path: str) -> Path:
        p = (self.ws / path).resolve()
        if not str(p).startswith(str(self.ws)):
            raise ValueError(f"path escapes workspace: {path}")
        return p

    def read_file(self, path: str, limit: int = 8000) -> str:
        try:
            p = self._safe(path)
            if not p.exists():
                return f"[error] file not found: {path}"
            return p.read_text(encoding="utf-8", errors="replace")[:limit]
        except Exception as e:
            return f"[error] {e}"

    def write_file(self, path: str, content: str) -> str:
        try:
            p = self._safe(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"[ok] wrote {p} ({len(content)} chars)"
        except Exception as e:
            return f"[error] {e}"

    def list_dir(self, path: str = ".") -> str:
        try:
            p = self._safe(path)
            if not p.exists():
                return f"[error] not found: {path}"
            items = [("D" if c.is_dir() else "F") + " " + c.name for c in sorted(p.iterdir())]
            return "\n".join(items) if items else "[empty]"
        except Exception as e:
            return f"[error] {e}"

    def run_shell(self, cmd: str) -> str:
        if not self.allow_shell:
            return "[blocked] shell disabled in config (set allow_shell: true)"
        base = cmd.strip().split()[0].lower()
        if base not in self.shell_allowlist:
            return f"[blocked] command not in allowlist: {base}"
        try:
            r = subprocess.run(
                cmd, shell=True, cwd=str(self.ws), capture_output=True, text=True, timeout=60
            )
            return (r.stdout or "") + (r.stderr or "")
        except Exception as e:
            return f"[error] {e}"
