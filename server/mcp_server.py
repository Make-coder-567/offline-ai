"""MCP server：把本机 Ollama 模型暴露为 WorkBuddy 可调用的工具（离线推理）。

WorkBuddy 通过 ~/.workbuddy/mcp.json 以 stdio 启动本文件后，
即可在对话中调用 local_llm_chat 等工具 —— 推理完全走本地，断网可用。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))

import yaml
from mcp.server.fastmcp import FastMCP

try:
    import ollama
except ImportError:
    ollama = None

cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
mcp = FastMCP("offline-ai")


def _gen(prompt: str, temperature: float = 0.3) -> str:
    if ollama is None:
        return "[error] ollama python lib not installed; run: pip install ollama"
    try:
        r = ollama.generate(
            model=cfg["model"], prompt=prompt, options={"temperature": temperature}
        )
        return r["response"]
    except Exception as e:
        return f"[error] ollama: {e}"


@mcp.tool()
def local_llm_chat(prompt: str) -> str:
    """调用本机离线大模型(Ollama)进行对话/文本生成。适用于隐私数据或断网场景。"""
    return _gen(prompt)


@mcp.tool()
def local_llm_summarize(text: str, max_words: int = 200) -> str:
    """用本地模型对长文本做中文摘要，文本不出本机。"""
    p = f"请用不超过{max_words}字概括以下内容，中文输出：\n\n{text[:12000]}"
    return _gen(p, temperature=0.2)


@mcp.tool()
def local_llm_structured(prompt: str, format_hint: str = "") -> str:
    """用本地模型按要求生成结构化文本（表格/代码/JSON），返回纯文本结果。"""
    p = f"{prompt}\n请只输出结果内容，不要解释。"
    if format_hint:
        p += f"\n格式要求: {format_hint}"
    return _gen(p, temperature=0.2)


if __name__ == "__main__":
    mcp.run()
