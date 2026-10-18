"""长期记忆：一份可增删查的 Markdown 事实库（本机文件，不联网）。

落盘：<data_dir>/memory.md（开发态 chatui/data/memory.md，打包态 %LOCALAPPDATA%）

每条一行：`- [2026-10-18] 事实内容`，零依赖、可直接用编辑器看/改。
检索用简单的关键词命中打分（记忆条目不多，没必要上向量）。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

MAX_FACTS = 500
MAX_FACT_CHARS = 300
_LINE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\] (.*)$")


def memory_path(data_dir) -> Path:
    return Path(data_dir) / "memory.md"


def _read(path: Path) -> list:
    if not path.is_file():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE.match(ln.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _write(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["# 长期记忆", "",
            "> 由离线AI自动维护的事实库；可直接编辑本文件增删条目。", ""]
    body += ["- [%s] %s" % (d, t) for d, t in rows]
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    import os
    os.replace(tmp, path)


def remember(data_dir, fact: str) -> str:
    """记住一条事实；完全相同的事实不重复记录。"""
    fact = " ".join((fact or "").split()).strip()
    if not fact:
        return "[error] 空内容"
    if len(fact) > MAX_FACT_CHARS:
        fact = fact[:MAX_FACT_CHARS]
    p = memory_path(data_dir)
    rows = _read(p)
    if any(t == fact for _, t in rows):
        return "（这条已经记住了：%s）" % fact[:40]
    rows.append((time.strftime("%Y-%m-%d"), fact))
    if len(rows) > MAX_FACTS:
        rows = rows[-MAX_FACTS:]
    _write(p, rows)
    return "[ok] 已记住：%s" % fact[:60]


def forget(data_dir, keyword: str) -> str:
    """删除包含关键词的条目，返回删除条数。"""
    p = memory_path(data_dir)
    rows = _read(p)
    keep = [(d, t) for d, t in rows if keyword.lower() not in t.lower()]
    n = len(rows) - len(keep)
    if n:
        _write(p, keep)
    return "[ok] 删除 %d 条" % n


def recall(data_dir, query: str = "", top_k: int = 8) -> str:
    """按关键词召回；query 为空则列最近条目。"""
    p = memory_path(data_dir)
    rows = _read(p)
    if not rows:
        return "（记忆库还是空的）"
    if query:
        keys = [k for k in re.split(r"[\s，,。、]+", query) if len(k) >= 2]
        scored = []
        for d, t in rows:
            s = sum(1 for k in keys if k in t)
            if s:
                scored.append((s, d, t))
        scored.sort(key=lambda x: (-x[0], x[1]))
        rows = [(d, t) for _s, d, t in scored[:max(1, top_k)]]
        if not rows:
            return "（记忆里没有和「%s」相关的内容）" % query
    else:
        rows = rows[-max(1, top_k):]
    return "\n".join("- [%s] %s" % (d, t) for d, t in rows)


def as_system_note(data_dir, top_k: int = 8) -> str:
    """把最近的事实拼成一段可注入系统提示词的文本。"""
    p = memory_path(data_dir)
    rows = _read(p)[-max(1, top_k):]
    if not rows:
        return ""
    return "已知的用户偏好/事实：\n" + "\n".join("- %s" % t for _d, t in rows)
