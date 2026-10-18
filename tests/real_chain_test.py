# -*- coding: utf-8 -*-
"""真实链路验证（需要本机 Ollama 在跑；smoke_test.py 是纯离线的，不含本文件）。

覆盖 Phase 1 三条端到端链路：
  1. 文档问答：扫描件/长文档 → 解析（含 OCR）→ 检索 → 本地模型作答并附引用
  2. Agent 行动：写文件 → 审计留痕 → 一键回滚
  3. 深度思考：草稿 → 自检 → 定稿（约 3 倍耗时，默认跑，--quick 可跳过）

用法：
  python tests/real_chain_test.py            # 全跑
  python tests/real_chain_test.py --quick    # 跳过深度思考
Ollama 不可用时自动跳过并提示，不算失败。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")

OK, FAIL = [], []


def step(name, fn):
    try:
        msg = fn()
        OK.append(name)
        print("[OK]  %s%s" % (name, ("  — " + str(msg)) if msg else ""), flush=True)
    except Exception as e:
        FAIL.append(name)
        print("[FAIL] %s : %s" % (name, e), flush=True)


def ollama_alive() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(os.environ["OLLAMA_HOST"] + "/api/tags", timeout=3):
            return True
    except Exception:
        return False


def t_doc_qa():
    """扫描 PDF → OCR → 检索 → 作答（带 [1] 引用）。"""
    import server.mcp_server as M
    src = Path(tempfile.gettempdir()) / "docsample"
    pdf = src / "扫描件.pdf"
    if not pdf.is_file():                      # 没有样本就自己造一张
        from PIL import Image, ImageDraw, ImageFont
        src.mkdir(parents=True, exist_ok=True)
        fnt = ImageFont.truetype(r"C:\Windows\Fonts\simhei.ttf", 40)
        img = Image.new("RGB", (900, 200), "white")
        ImageDraw.Draw(img).text((40, 60), "离线智能助手验证样本", font=fnt, fill="black")
        img.save(pdf, "PDF", resolution=150.0)
    M.cfg["workspace_root"] = str(src)
    M._RAG = None
    M.local_rag_index(force=True)
    ans = M.local_doc_ask("扫描件.pdf", "这份扫描件里写了什么？")
    assert "验证样本" in ans or "离线" in ans, ans[:200]
    assert "[1]" in ans, "回答应带引用编号"
    return ans.strip().splitlines()[0][:60]


def t_agent_write_undo():
    """Agent 写文件 → 审计 → 回滚。"""
    import yaml
    from bridge.protocol import Task
    from tools import Tools
    from audit import Audit
    import agent as ag

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    ws = tempfile.mkdtemp(prefix="chain_")
    (Path(ws) / "notes").mkdir()
    (Path(ws) / "notes" / "raw.md").write_text(
        "传感器 A 读数 12.5\n传感器 B 读数 9.8\n", encoding="utf-8")
    cfg["workspace_root"] = ws
    aud = Audit(ws)
    tools = Tools(ws, allow_python=False, audit=aud)
    out = ag.handle_task(cfg, None, Task(
        task_id="chain1",
        prompt="把 notes/raw.md 里两个传感器的读数列成一行，写入 notes/summary.md，"
               "完成后用 finish 说明你写了什么。"), tools)
    target = Path(ws) / "notes" / "summary.md"
    assert target.is_file(), "Agent 未能写出文件：%s" % out[:200]
    rows = aud.tail(5)
    assert rows and rows[-1]["action"] == "write_file"
    assert aud.rollback().startswith("[ok]")
    assert not target.exists(), "回滚后文件应被撤销"
    shutil.rmtree(ws, ignore_errors=True)
    return "写入→审计→回滚 全部成功"


def t_deep_think():
    import server.mcp_server as M
    q = "用三句话说明 BM25 相比 TF-IDF 有什么改进。"
    t0 = time.time()
    deep = M.local_llm_chat(q, deep=True)
    dt = time.time() - t0
    assert len(deep) > 30 and not deep.startswith("[error]"), deep[:200]
    return "深度思考 %.0fs，%d 字" % (dt, len(deep))


def main():
    if not ollama_alive():
        print("跳过：Ollama 未运行（%s）。启动后重试。" % os.environ["OLLAMA_HOST"])
        return 0
    step("文档问答（OCR→检索→引用）", t_doc_qa)
    step("Agent 写文件 + 审计 + 回滚", t_agent_write_undo)
    if "--quick" not in sys.argv:
        step("深度思考（草稿→自检→定稿）", t_deep_think)
    print("=" * 50)
    print("OK %d / FAIL %d" % (len(OK), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
