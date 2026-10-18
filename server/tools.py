"""离线AI 工具集：受限的文件 / 工作区访问。

所有路径被限制在 workspace_root 内（防越权）。

安全约束（2026-10-17 审查修复，详见 docs/代码审查报告_2026-10-17.md）：
  - _safe 用 is_relative_to 做路径分量级校验，杜绝 "<ws>-evil" 同级前缀绕过；
  - 敏感文件（.env/.git/密钥等）禁止读取，防 prompt 注入导致泄露；
  - write_file 禁止覆盖配置类文件（config.yaml/mcp.json），单文件上限 1MB，
    且写前先快照（server/audit.py），可一键回滚；
  - shell 执行已移除（等价于 RCE），代之以受限的 run_python 沙箱：
    默认关闭（需 config.yaml 显式开启），AST 静态检查禁止危险 import 与
    文件/网络/进程访问，子进程 -I 隔离 + 超时强杀，全程记审计日志。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


class Tools:
    # 敏感文件：按路径分量精确匹配（避免 "tokenizer.py" 被 "token" 误伤）
    _SENSITIVE_NAMES = {
        ".env", ".git", ".ssh", "id_rsa", "id_ed25519", "secrets",
        "credentials", ".netrc", ".pgpass", ".bashrc", ".zshrc",
    }
    _SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}
    # write_file 禁止覆盖的文件（防改配置开后门）
    _PROTECTED_NAMES = {"config.yaml", "config.yml", "mcp.json"}
    _MAX_WRITE_CHARS = 1 << 20  # 单文件写入上限 1MB
    _SEARCH_EXTS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".csv",
                    ".log", ".ini", ".toml", ".html", ".xml", ".js", ".ts"}
    _SKIP_SEARCH_DIRS = {".git", "node_modules", "__pycache__", ".offline-audit",
                         ".offline-rag", ".offline-bridge", "gguf_models",
                         "bundle_models", "dist", "build", ".venv", "venv"}
    _MAX_SEARCH_FILES = 600
    _MAX_SEARCH_HITS = 30

    # ---- run_python 沙箱：只做纯计算，不给文件/网络/进程能力 ----
    _PY_DENY_MODULES = {
        "os", "sys", "subprocess", "socket", "ctypes", "importlib", "shutil",
        "webbrowser", "pickle", "multiprocessing", "signal", "pty", "platform",
        "requests", "urllib", "urllib2", "http", "ftplib", "smtplib", "telnetlib",
        "paramiko", "asyncio", "threading", "tempfile", "glob", "sqlite3",
    }
    _PY_DENY_CALLS = {"eval", "exec", "compile", "__import__", "open", "input",
                      "exit", "quit", "help", "globals", "locals", "vars"}

    def __init__(self, workspace_root: str, allow_python: bool = False, audit=None):
        self.ws = Path(workspace_root).resolve()
        self.allow_python = bool(allow_python)
        self.audit = audit

    def _safe(self, path: str) -> Path:
        p = (self.ws / path).resolve()
        # 路径分量级校验：is_relative_to 不会被 "<ws>-evil" 这类同级前缀目录绕过
        if not p.is_relative_to(self.ws):
            raise ValueError(f"path escapes workspace: {path}")
        return p

    def _sensitive(self, p: Path) -> bool:
        if p.suffix.lower() in self._SENSITIVE_SUFFIXES:
            return True
        # 同时比对全名与主干：secrets.json / credentials.yaml 这类"文件名像密钥"的一律拦掉
        if p.name.lower() in self._SENSITIVE_NAMES or p.stem.lower() in self._SENSITIVE_NAMES:
            return True
        return any(part.lower() in self._SENSITIVE_NAMES for part in p.parts)

    def read_file(self, path: str, limit: int = 8000) -> str:
        try:
            p = self._safe(path)
            if self._sensitive(p):
                return f"[blocked] sensitive file: {path}"
            if not p.exists():
                return f"[error] file not found: {path}"
            return p.read_text(encoding="utf-8", errors="replace")[:limit]
        except Exception as e:
            return f"[error] {e}"

    def write_file(self, path: str, content: str, task_id: str = "") -> str:
        try:
            if len(content) > self._MAX_WRITE_CHARS:
                return f"[error] content too large: {len(content)} > {self._MAX_WRITE_CHARS} chars"
            p = self._safe(path)
            if p.name in self._PROTECTED_NAMES:
                return f"[blocked] protected file: {path}"
            if self._sensitive(p):
                return f"[blocked] sensitive file: {path}"
            snap = self.audit.snapshot(p) if self.audit else ""
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            if self.audit:
                self.audit.log("write_file", path, "%d chars" % len(content),
                               snap=snap, task_id=task_id)
            tip = "（可用回滚撤销）" if snap else "（新建文件，回滚即删除）"
            return f"[ok] wrote {path} ({len(content)} chars){tip}"
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

    def search_text(self, pattern: str, path: str = ".", max_hits: int = None,
                    regex: bool = False) -> str:
        """在工作区文本里搜关键词，返回 "文件:行号: 内容" 列表。"""
        if not pattern:
            return "[error] empty pattern"
        cap = int(max_hits or self._MAX_SEARCH_HITS)
        try:
            base = self._safe(path)
        except Exception as e:
            return f"[error] {e}"
        if not base.exists():
            return f"[error] not found: {path}"
        import re as _re
        try:
            rx = _re.compile(pattern) if regex else None
        except Exception as e:
            return f"[error] 正则表达式非法: {e}"

        files = [base] if base.is_file() else []
        if not files:
            for p in base.rglob("*"):
                if any(d in self._SKIP_SEARCH_DIRS for d in p.parts):
                    continue
                if not p.is_file() or p.suffix.lower() not in self._SEARCH_EXTS:
                    continue
                if self._sensitive(p):
                    continue
                files.append(p)
                if len(files) >= self._MAX_SEARCH_FILES:
                    break
        hits, scanned = [], 0
        for p in files:
            if len(hits) >= cap:
                break
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:200_000]
            except Exception:
                continue
            scanned += 1
            try:
                rel = str(p.relative_to(self.ws)).replace("\\", "/")
            except Exception:
                rel = str(p)
            for i, line in enumerate(text.splitlines(), 1):
                ok = bool(rx.search(line)) if rx else (pattern in line)
                if ok:
                    hits.append("%s:%d: %s" % (rel, i, line.strip()[:200]))
                    if len(hits) >= cap:
                        break
        if not hits:
            return f"（未找到「{pattern}」，已扫描 {scanned} 个文件）"
        head = "找到 %d 处（扫描 %d 个文件）：\n" % (len(hits), scanned)
        return head + "\n".join(hits)

    # ---------- 受限 Python 沙箱（默认关闭） ----------
    def _py_guard(self, code: str) -> str:
        """AST 静态检查：禁止危险 import / 内建调用 / dunder 逃逸。返回 "" 表示放行。"""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"[error] 代码语法错误: {e}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = (a.name or "").split(".")[0]
                    if top in self._PY_DENY_MODULES:
                        return f"[blocked] 沙箱禁止 import {top}"
            elif isinstance(node, ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                if top in self._PY_DENY_MODULES:
                    return f"[blocked] 沙箱禁止 import {top}"
            elif isinstance(node, ast.Name):
                if node.id in self._PY_DENY_CALLS:
                    return f"[blocked] 沙箱禁止使用 {node.id}()"
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                return f"[blocked] 沙箱禁止访问 {node.attr}（可绕过限制）"
        return ""

    def run_python(self, code: str, timeout: int = 10, task_id: str = "") -> str:
        """在隔离子进程里跑一段"纯计算"Python：无文件/网络/进程能力。

        默认关闭（Tools(allow_python=True) 或 config.yaml: agent.allow_python=true）。
        """
        if not self.allow_python:
            return ("[blocked] run_python 未开启。如需让智能体执行计算代码，"
                    "请在 config.yaml 设置 agent.allow_python: true（风险自负，"
                    "所有执行都会写入 .offline-audit 审计日志）。")
        code = (code or "").strip()
        if not code:
            return "[error] empty code"
        if len(code) > 20000:
            return "[error] code too large (max 20000 chars)"
        blocked = self._py_guard(code)
        if blocked:
            if self.audit:
                self.audit.log("run_python", "", blocked[:200], ok=False, task_id=task_id)
            return blocked

        exe = sys.executable
        if getattr(sys, "frozen", False):        # 打包态 sys.executable 是 app 本身
            import shutil
            exe = shutil.which("python") or shutil.which("python3") or exe
        try:
            r = subprocess.run([exe, "-I", "-c", code],
                               capture_output=True, text=True, timeout=max(1, int(timeout)),
                               cwd=str(self.ws),
                               env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1"})
            out = ((r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr else "")).strip()
            if len(out) > 4000:
                out = out[:4000] + "\n…（输出已截断）"
            res = "[ok] rc=%d\n%s" % (r.returncode, out or "（无输出）")
        except subprocess.TimeoutExpired:
            res = f"[error] 执行超时（>{timeout}s），已终止"
        except Exception as e:
            res = f"[error] {e}"
        if self.audit:
            self.audit.log("run_python", "", code[:300],
                           ok=not res.startswith("[error]"), task_id=task_id)
        return res
