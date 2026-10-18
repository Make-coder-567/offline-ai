"""统一的数据目录解析：与 chatui/chat_app.py 的 _data_dir() 保持一致。

打包态：%LOCALAPPDATA%/offline_chat_data
开发态：<项目根>/chatui/data

会话、长期记忆、定时任务都落在这里，避免污染用户工作区，
也避免"MCP 进程"和"界面进程"算出两个不同的目录。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        d = Path(base) / "offline_chat_data"
    else:
        d = Path(__file__).resolve().parent.parent / "chatui" / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_workspace(cfg: dict) -> str:
    """把 workspace_root 归一化成绝对路径。

    config.yaml 里写相对路径（默认 "."）时，按**项目根**解析，
    这样别人 clone 到任何目录都能直接用，不会落到进程 CWD。
    """
    raw = str((cfg or {}).get("workspace_root") or ".")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = project_root() / p
    return str(p.resolve())
