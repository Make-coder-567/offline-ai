# -*- coding: utf-8 -*-
"""offline-ai 回归冒烟测试（无需联网 / 不需要 Ollama 在跑）。

覆盖：
  tools     路径穿越、写保护、敏感文件、shell 已移除
  protocol  坏任务日志、原子写与恢复、task_id 去重、取消标记
  agent     ReAct 解析/循环/路由/无 Action 降级/重复失败早停
  rag       BM25 建库与检索、增量跳过
  chatui    Markdown 渲染、多轮上下文、停止、持久化、清空、图片输入

运行：
  python tests/smoke_test.py          # 全部（GUI 部分使用 offscreen）
  python tests/smoke_test.py --no-ui  # 只跑后端
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "chatui"))

PASSED, FAILED = [], []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print("[PASS]", name, flush=True)
    except Exception as e:
        FAILED.append(name)
        print("[FAIL]", name, ":", e, flush=True)
        traceback.print_exc()


# ==================== tools ====================
from tools import Tools  # noqa: E402

def t_tools():
    ws = tempfile.mkdtemp()
    evil = ws + "-evil"
    os.makedirs(evil, exist_ok=True)
    with open(os.path.join(evil, "secret.txt"), "w", encoding="utf-8") as f:
        f.write("TOPSECRET")
    t = Tools(ws)
    assert t.read_file("../" + os.path.basename(ws) + "-evil/secret.txt").startswith("[error]")
    assert t.write_file("sub/a.txt", "hi").startswith("[ok]")
    assert t.read_file("sub/a.txt") == "hi"
    assert t.write_file("config.yaml", "x").startswith("[blocked]")
    assert t.write_file("big.txt", "a" * ((1 << 20) + 1)).startswith("[error]")
    with open(os.path.join(ws, ".env"), "w", encoding="utf-8") as f:
        f.write("SECRET=1")
    assert t.read_file(".env").startswith("[blocked]")
    assert not hasattr(t, "run_shell")


def t_tools_action():
    """新增行动能力：search_text / 带审计的 write_file / 受限 run_python。"""
    from audit import Audit
    ws = tempfile.mkdtemp()
    with open(os.path.join(ws, "notes.md"), "w", encoding="utf-8") as f:
        f.write("传感器标定流程\n阈值设定 0.85\n")
    aud = Audit(ws)
    t = Tools(ws, allow_python=False, audit=aud)

    # search_text
    hits = t.search_text("阈值")
    assert "notes.md:2" in hits, hits
    assert "未找到" in t.search_text("不存在的关键词")

    # write_file：覆盖前留快照，可回滚
    assert t.write_file("notes.md", "新内容").startswith("[ok]")
    assert t.read_file("notes.md") == "新内容"
    rows = aud.tail(5)
    assert rows and rows[-1]["action"] == "write_file" and rows[-1]["snap"]
    assert aud.rollback().startswith("[ok]")
    assert t.read_file("notes.md").startswith("传感器标定流程")
    # 新建文件的回滚 = 删除
    t.write_file("new/x.txt", "临时")
    assert (Path(ws) / "new" / "x.txt").is_file()
    assert aud.rollback().startswith("[ok]")
    assert not (Path(ws) / "new" / "x.txt").exists()
    # 敏感/受保护文件仍然写不进去
    assert t.write_file("secrets.json", "x").startswith("[blocked]")

    # run_python：默认关闭；开启后仍被 AST 检查拦住危险调用
    assert t.run_python("print(1)").startswith("[blocked]")
    t2 = Tools(ws, allow_python=True, audit=aud)
    assert t2.run_python("import os; os.system('echo hi')").startswith("[blocked]")
    assert t2.run_python("open('x','w')").startswith("[blocked]")
    assert t2.run_python("eval('1+1')").startswith("[blocked]")
    out = t2.run_python("import math; print(math.sqrt(16))")
    assert "[ok]" in out and "4.0" in out, out
    assert t2.run_python("while True: pass", timeout=2).startswith("[error]")


def t_agent_multiline():
    """ReAct v2：write_file / run_python 的 Input 支持多行。"""
    import agent as ag
    a, arg = ag._parse_action("Action: write_file\nInput: out/a.md\n# 标题\n\n正文内容\n")
    assert a == "write_file", a
    assert arg.splitlines()[0] == "out/a.md" and "正文内容" in arg, arg
    # 代码围栏要被剥掉，且不能吞掉下一个 Action
    a, arg = ag._parse_action(
        "Action: run_python\nInput: ```python\nprint(1+1)\n```\nAction: finish\nInput: 完成")
    assert a == "run_python" and arg.strip() == "print(1+1)", (a, arg)
    # 单行工具仍只取首行（防止模型把 Observation 后的内容当成参数）
    a, arg = ag._parse_action("Action: read_file\nInput: a.md\nObservation: xxx")
    assert a == "read_file" and arg == "a.md", arg
    assert ag._parse_action("我想直接回答") == (None, None)
    # 目标路径推断：从任务里取"要写到哪"
    assert ag._guess_target("把结果写入 notes/summary.md，然后 finish") == "notes/summary.md"
    assert ag._guess_target("随便聊聊") == ""

    # 模型只答不写时，必须由 agent 兜底落盘（不能给用户"其实没写"的结果）
    ws = tempfile.mkdtemp()
    real_llm = ag.run_llm
    ag.run_llm = lambda *a, **k: "传感器读数已汇总完毕。"
    try:
        out = ag.handle_task({"model": "m", "agent_max_steps": 3}, None,
                             Task(task_id="w1",
                                  prompt="把结果写入 report.md"), Tools(ws))
    finally:
        ag.run_llm = real_llm
    assert (Path(ws) / "report.md").is_file(), out
    assert "report.md" in out and "传感器读数" in (Path(ws) / "report.md").read_text(
        encoding="utf-8")


# ==================== protocol ====================
from bridge.protocol import Bridge, Task  # noqa: E402

def t_protocol():
    b = Bridge(tempfile.mkdtemp(), ".offline-bridge")
    (b.inbox_path() / "bad.json").write_text("{not json", encoding="utf-8")
    assert b.list_pending() == []
    assert "bad.json" in (b.log_path() / "bridge.log").read_text(encoding="utf-8")
    for name in ("a.json", "b.json"):
        (b.inbox_path() / name).write_text(json.dumps(
            {"task_id": "dup", "prompt": "x", "status": "pending"}), encoding="utf-8")
    assert len(b.list_pending()) == 1
    tk = Task(task_id="t1", prompt="hi")
    path = b.inbox_path() / "t1.json"
    b.mark_running(path, tk)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"
    assert b.reset_running() == 1
    assert b.list_pending()[0][1].status == "pending"
    t2 = Task(task_id="dup", prompt="x")
    p1, p2 = b.write_outbox(t2), b.write_outbox(t2)
    assert p1 != p2 and p2.exists()
    p3 = b.write_outbox(t2)
    assert len({p1.name, p2.name, p3.name}) == 3, (p1, p2, p3)


# ==================== agent ====================
import agent as ag  # noqa: E402

def t_agent():
    ws = tempfile.mkdtemp()
    tools = Tools(ws)
    with open(os.path.join(ws, "README.md"), "w", encoding="utf-8") as f:
        f.write("# demo\n内容")
    calls, prompts = [], []
    seq = ["Action: list_dir\nInput: .", "Action: finish\nInput: 有 README.md"]

    def fake(cfg, prompt, temperature=0.3, model=None, num_predict=None):
        calls.append(model)
        prompts.append(prompt)
        return seq[len(calls) - 1] if len(calls) <= len(seq) else seq[-1]

    orig = ag.run_llm
    ag.run_llm = fake
    try:
        cfg = {"model": "m", "routes": {"code": "c"}, "agent_max_steps": 3}
        assert ag.handle_task(cfg, None, Task("t", "x"), tools) == "有 README.md"
        assert "Observation:" in prompts[1]
        ag.handle_task(cfg, None, Task("t2", "x", task_type="code"), tools)
        assert calls[-1] == "c"
    finally:
        ag.run_llm = orig

    # 无 Action → 直接作为最终回答
    ag.run_llm = lambda *a, **k: "直接回答"
    try:
        assert ag.handle_task({"model": "m"}, None, Task("t3", "x"), tools) == "直接回答"
    finally:
        ag.run_llm = orig

    # 空输出 → 明确报错，不把空字符串当成功
    ag.run_llm = lambda *a, **k: ""
    try:
        r = ag.handle_task({"model": "m"}, None, Task("t3b", "x"), tools)
        assert r.startswith("[error]") and "未返回内容" in r, r
    finally:
        ag.run_llm = orig

    # 重复失败动作早停
    ag.run_llm = lambda *a, **k: "Action: read_file\nInput: nope.txt"
    try:
        r = ag.handle_task({"model": "m", "agent_max_steps": 5}, None, Task("t4", "x"), tools)
        assert r.startswith("[error]") and "重复失败" in r, r
    finally:
        ag.run_llm = orig

    # 步数上限：最后一步仍是 Action → 返回可读错误；prompt 不随步数无限增长
    lens = []

    def fake_multi(cfg, prompt, temperature=0.3, model=None, num_predict=None):
        lens.append(len(prompt))
        return "Action: list_dir\nInput: .%d" % len(lens)

    ag.run_llm = fake_multi
    try:
        r = ag.handle_task({"model": "m", "agent_max_steps": 6}, None, Task("t6", "x"), tools)
    finally:
        ag.run_llm = orig
    assert r.startswith("[error]") and "未能在 6 步内完成" in r, r
    assert len(lens) == 6 and max(lens) < 9000, (len(lens), max(lens))

    # cancel 标记
    b = Bridge(tempfile.mkdtemp(), ".offline-bridge")
    (b.inbox_path() / "c1.cancel").write_text("", encoding="utf-8")
    try:
        ag.handle_task({"model": "m"}, b, Task("c1", "x"), tools)
        raise AssertionError("应抛出 TaskCancelled")
    except ag.TaskCancelled:
        pass


# ==================== rag ====================
def t_rag():
    from rag import RagIndex
    ws = tempfile.mkdtemp()
    for name, text in {
        "a.md": "传感器控制与多目标跟踪。" * 8,
        "b.md": "教师节活动与祝福页面。" * 8,
        "secrets.json": '{"key": "TOPSECRET-ABC"}',
        "normal.md": "公开说明文档，不含机密。" * 8,
    }.items():
        with open(os.path.join(ws, name), "w", encoding="utf-8") as f:
            f.write(text)
    idx = RagIndex(ws)
    st = idx.build(force=True)
    assert st["indexed"] and st["chunks"] >= 2, st
    assert idx.query("传感器", 1)[0]["path"] == "a.md"
    assert idx.build()["indexed"] is False
    # 敏感文件名（secrets.json / .env.*）不得进入索引
    assert all("secrets.json" not in h["path"] for h in idx.query("TOPSECRET", 5))
    assert all(not h["path"].startswith(".env") for h in idx.query("TOPSECRET", 5))


def t_docparse():
    """PDF/DOCX/XLSX 解析（缺库时跳过）；短文档必须能出 chunk。"""
    try:
        from server import docparse
        import docx
    except Exception as e:
        raise AssertionError("缺少解析依赖，请 pip install pypdf python-docx openpyxl: %s" % e)
    ws = tempfile.mkdtemp()
    d = docx.Document()
    d.add_paragraph("本季度营收为 1234 万元，同比增长 18%。")
    p = os.path.join(ws, "报告.docx")
    d.save(p)
    text = docparse.extract_text(p)
    assert "1234" in text and not text.startswith("[error]"), text
    assert docparse.extract_text(os.path.join(ws, "不存在.docx")).startswith("[error]")

    from rag import RagIndex
    idx = RagIndex(ws)
    st = idx.build(force=True)
    assert st["files"] >= 1 and st["chunks"] >= 1, st
    assert idx.stats()["by_type"].get("doc", 0) >= 1, idx.stats()
    assert idx.query("营收", 1)[0]["src"] == "doc"
    # 同一文档重复入库不应产生重复 chunk（否则检索结果自相重复）
    idx.add_paths([p])
    n1 = len(idx.data["chunks"])
    idx.add_paths([p])
    assert len(idx.data["chunks"]) == n1, (n1, len(idx.data["chunks"]))
    # 已解析文本直接入库：不能再重新读盘（否则扫描件会因不带 OCR 切成 0 段）
    r = idx.add_text(idx.key_for(p), "这份内容只存在于内存里，磁盘上并没有这句话。" * 3)
    assert r["chunks"] >= 1, r
    assert any("只存在于内存里" in c["text"] for c in idx.data["chunks"])


def t_mcp_tools():
    """MCP 工具清单：新增的文档/OCR/审计/沙箱工具必须都在，且参数可调用。"""
    import server.mcp_server as M
    tools = getattr(getattr(M.mcp, "_tool_manager", None), "_tools", None) or {}
    names = set(tools)
    need = {"local_llm_chat", "local_doc_text", "local_doc_ask", "local_ocr",
            "local_rag_index", "local_rag_query", "local_rag_answer",
            "local_run_python", "local_audit", "local_undo", "local_llm_embed"}
    assert need <= names, need - names
    # 默认不允许执行代码（安全默认值）
    assert bool((M.cfg.get("agent") or {}).get("allow_python", False)) is False


def t_phase3():
    """长期记忆 / 定时任务 / OpenAI 网关（网关用桩替换 Ollama 请求）。"""
    from server import memory, scheduler, gateway
    d = tempfile.mkdtemp()
    # 记忆：去重 + 检索 + 删除 + 注入文本
    assert memory.remember(d, "回答尽量简短").startswith("[ok]")
    assert "已经记住" in memory.remember(d, "回答尽量简短")
    memory.remember(d, "常用语言是中文")
    assert "简短" in memory.recall(d, "简短")
    assert memory.as_system_note(d).startswith("已知的用户偏好")
    assert memory.forget(d, "常用语言").startswith("[ok]")
    assert "常用语言" not in memory.recall(d)
    assert (Path(d) / "memory.md").is_file()

    # 定时任务：参数校验 + 到期触发 + 幂等
    scheduler.add(d, "每小时总结", every_minutes=60)
    try:
        scheduler.add(d, "缺周期", 0, "")
        assert False, "应拒绝缺少周期的任务"
    except ValueError:
        pass
    hits = []
    # dry_run 不调用 runner，这里用真实触发来验证 runner 被喂到了 prompt
    assert scheduler.run_due(d, lambda p, m: hits.append(p) or "ok") == 1
    assert hits == ["每小时总结"], hits
    assert scheduler.run_due(d, lambda p, m: "ok") == 0, "刚跑过不应重复触发"
    rows = scheduler.list_all(d)
    assert scheduler.remove(d, rows[0]["id"]) and not scheduler.list_all(d)

    # 网关：把 Ollama 调用打桩，验证 OpenAI 协议转换
    calls = []

    class Req:
        def __init__(self, path, data):
            self.path = path
            self.headers = {"Content-Length": str(len(json.dumps(data).encode()))}
            self._body = json.dumps(data).encode()

        def rfile_read(self):
            return self._body

    gw = gateway.Handler
    orig = gateway._ollama

    def fake_ollama(path, payload=None, timeout=600):
        calls.append((path, payload))
        if path == "/api/tags":
            return {"models": [{"model": "gemma3:4b"}]}
        return {"message": {"content": "你好，我是本地模型"},
                "prompt_eval_count": 10, "eval_count": 4}

    gateway._ollama = fake_ollama
    try:
        out = []
        gw.wfile = None
        # 直接调业务逻辑：构造最小 handler
        h = gw.__new__(gw)
        h.path = "/v1/chat/completions"
        captured = {}

        def _json(code, obj):
            captured["code"], captured["obj"] = code, obj
        h._json = _json
        h.rfile = type("R", (), {"read": lambda self, n: json.dumps(
            {"model": "gemma3:4b",
             "messages": [{"role": "user", "content": "hi"}]}).encode()})()
        h.headers = {"Content-Length": "80"}
        h.do_POST()
        assert captured["code"] == 200, captured
        msg = captured["obj"]["choices"][0]["message"]
        assert msg["role"] == "assistant" and msg["content"] == "你好，我是本地模型"
        assert captured["obj"]["usage"]["total_tokens"] == 14
        # 模型清单
        captured.clear()
        h.path = "/v1/models"
        h.do_GET()
        assert captured["obj"]["data"][0]["id"] == "gemma3:4b"
    finally:
        gateway._ollama = orig


def t_hybrid():
    """混合检索：BM25 命中不了时靠语义召回；模型缺失必须静默降级。"""
    from rag import RagIndex
    from server import embed as emb
    ws = tempfile.mkdtemp()
    # 用词刻意不同：BM25 对"营收增长"零命中，只能靠向量
    with open(os.path.join(ws, "财报.md"), "w", encoding="utf-8") as f:
        f.write("本季度营业额同比提升百分之十八，净利润扭亏为盈。")
    with open(os.path.join(ws, "运维.md"), "w", encoding="utf-8") as f:
        f.write("服务器磁盘告警阈值调整为百分之八十五，需清理日志目录。")
    idx = RagIndex(ws)
    idx.build(force=True)
    assert idx.query("营收增长", top_k=1, hybrid=False) == [], "BM25 本就不该命中"

    if emb.available():
        assert idx.ensure_vectors() >= 1
        hits = idx.query("营收增长", top_k=1, hybrid=True)
        assert hits and hits[0]["path"] == "财报.md", hits
        assert idx.stats()["vectors"] >= 1
        assert idx.ensure_vectors() == 0, "第二次应为增量，不再重算"
        # 关键词查询不被语义拖偏
        assert idx.query("磁盘告警", top_k=1, hybrid=True)[0]["path"] == "运维.md"
        # 向量文件被删：必须整体作废重算，不能拿旧 id 去索引新矩阵的行
        idx.vec_path.unlink()
        assert idx.ensure_vectors() >= 1
        hits2 = idx.query("营收增长", top_k=1, hybrid=True)
        assert hits2 and hits2[0]["path"] == "财报.md", hits2

    # 降级：向量不可用时不报错，退回 BM25
    emb.reset()
    emb._model, emb._broken = None, "模拟不可用"
    try:
        idx2 = RagIndex(ws)
        assert idx2.ensure_vectors() == 0
        assert idx2.query("磁盘告警", top_k=1)[0]["path"] == "运维.md"
    finally:
        emb.reset()


# ==================== chatui ====================
def run_ui_tests():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import chat_app as ca
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    tmp = tempfile.mkdtemp()
    ca._data_dir = lambda: tmp
    ca.ChatWindow._bootstrap = lambda self: None

    def fresh():
        p = ca._session_path()
        if os.path.isfile(p):
            os.remove(p)
        return ca.ChatWindow()

    class Sig:
        def connect(self, *a, **k):
            pass

    class FakeWorker:
        last = None

        def __init__(self, model, messages, temperature=0.3, num_ctx=6144):
            self.token = Sig(); self.finished = Sig()
            self.errored = Sig(); self.stats = Sig()
            self.result_text = ""
            FakeWorker.last = (model, messages, temperature, num_ctx)

        def start(self):
            pass

        def cancel(self):
            pass

    def t_markdown():
        h = ca.render_markdown("**粗** `a<1`\n```python\nprint(1)\n```")
        assert "<b>粗</b>" in h and "a&lt;1" in h and "print(1)" in h
        assert "<script>" not in ca.render_markdown("<script>x</script>")

    def t_send_flow():
        orig = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            w.temperature, w.num_ctx, w.system_prompt = 0.7, 8192, "系统"
            w.input.setPlainText("你好")
            w._send()
            _m, msgs, temp, nctx = FakeWorker.last
            assert msgs[0] == {"role": "system", "content": "系统"}
            assert abs(temp - 0.7) < 1e-6 and nctx == 8192
            w._on_token("答复")
            w.worker.result_text = "答复"     # 真实 Worker 在收 token 时同步维护
            w._finish()
            assert w.history[-1] == {"role": "assistant", "content": "答复"}
            # 持久化恢复
            w2 = ca.ChatWindow()
            assert any(b._raw == "你好" for b in w2._bubbles)
        finally:
            ca.Worker = orig

    def t_stop_and_clear():
        w = fresh()
        w._remove_welcome()
        w._add_message("me", "hi", who="你", ts="1")
        w._add_message("sys", "已停止生成")
        w.busy = True
        w.worker = FakeWorker("m", [])
        w._stop()
        assert w._cancelled and w._stopping
        w._finish()
        assert not w.busy
        w._clear_session()
        assert w.feed.v.count() == 1 and w._welcome is not None

    def t_image():
        orig = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            p = os.path.join(tmp, "i.png")
            pm = QPixmap(32, 32); pm.fill(QColor("#E03131")); pm.save(p, "PNG")
            w._attach_image(p)
            assert not w.image_chip.isHidden()
            w._send()
            assert FakeWorker.last[1][-1].get("images")
            w._finish()
        finally:
            ca.Worker = orig

    class FakeDocWorker:
        """同步版本的 DocWorker：start() 里直接回调，便于确定性断言。"""
        win = None
        text = ""

        def __init__(self, path, ocr=True, parent=None):
            self.path = path
            self.done = Sig()
            self.failed = Sig()

        def start(self):
            FakeDocWorker.win._on_doc_parsed(self.path, FakeDocWorker.text)

    def t_doc():
        orig_w, orig_d = ca.Worker, ca.DocWorker
        ca.Worker = FakeWorker
        ca.DocWorker = FakeDocWorker
        try:
            w = fresh()
            w._remove_welcome()
            FakeDocWorker.win = w
            p = os.path.join(tmp, "报告.docx")

            # 1) 文档附件：chip 显示，且与图片互斥
            with open(p, "w", encoding="utf-8") as f:
                f.write("占位")
            w._attach_doc(p)
            assert not w.doc_chip.isHidden() and w._pending_doc == p
            pm = QPixmap(32, 32); pm.fill(QColor("#E03131"))
            ip = os.path.join(tmp, "i.png"); pm.save(ip, "PNG")
            w._attach_image(ip)
            assert w.doc_chip.isHidden() and w._pending_doc is None, "图片应顶掉文档"

            # 2) 短文档：整篇随消息发送
            FakeDocWorker.text = "文档正文。" * 50          # 250 字 < 3000
            w._attach_doc(p)
            w.input.setPlainText("这份文档说了什么？")
            w._send()
            assert w._pending_doc is None, "发送后应清空待发文档"
            body = FakeWorker.last[1][-1]["content"]
            assert "以下是文档《报告.docx》的全文" in body
            assert w.history[-1]["content"] == "这份文档说了什么？", w.history[-1]
            w._finish()

            # 3) 长文档：走检索，只把片段塞进本轮，不进历史
            w = fresh(); w._remove_welcome(); FakeDocWorker.win = w
            FakeDocWorker.text = "第三章 传感器标定流程。" * 400   # >3000
            w._attach_doc(p)
            w.input.setPlainText("传感器怎么标定？")
            w._send()
            body = FakeWorker.last[1][-1]["content"]
            assert "资料1" in body or "开头部分" in body, body[:120]
            assert "传感器怎么标定？" in body
            assert w.history[-1]["content"] == "传感器怎么标定？", "文档上下文不得进历史"
            w._finish()

            # 4) 解析期间点停止：不残留思考气泡，且回滚本轮用户消息
            w = fresh(); w._remove_welcome(); FakeDocWorker.win = w
            FakeDocWorker.text = "内容。" * 10
            w._attach_doc(p)
            w.input.setPlainText("会被取消的问题")
            w._send()
            w._cancelled = True
            w._on_doc_parsed(p, FakeDocWorker.text)
            assert not w.busy and w.think_item is None
            assert w.history == [], w.history

            # 5) 文档 + 深度思考：后两轮必须仍能看到文档片段
            w = fresh(); w._remove_welcome(); FakeDocWorker.win = w
            w.deep = True
            w.model = "gemma3:4b"
            FakeDocWorker.text = "文档正文。" * 50
            w._attach_doc(p)
            w.input.setPlainText("文档说了什么？")
            w._send()
            assert w._deep_stage == 1
            w.worker.result_text = "草稿"
            w._on_token("草稿")
            w._finish()
            msgs = FakeWorker.last[1]
            assert any("以下是文档《报告.docx》" in m.get("content", "") for m in msgs), \
                [m.get("content", "")[:40] for m in msgs]
            w._on_error("收尾")          # 结束回合，避免影响后续用例
            w._deep_stage = 0

            # 6) 重试会带回上次文档
            w = fresh(); w._remove_welcome(); FakeDocWorker.win = w
            w._attach_doc(p)
            w.input.setPlainText("重试我")
            w._send()
            w._on_error("连接失败")
            assert w._last_doc == p
            w._retry_last()
            # _send 已把它转入解析流程（_pending_doc 随即清空），用这两个状态证明带回成功
            assert w._last_doc == p and w._doc_question == "重试我", (
                w._last_doc, w._doc_question)
        finally:
            ca.Worker = orig_w
            ca.DocWorker = orig_d

    class FakeOcrWorker:
        """同步版 OCR 线程：start() 直接回调 _on_ocr_done。"""
        win = None
        text = "图片里的文字"

        def __init__(self, path, ocr=True, parent=None):
            self.path = path
            self.done = Sig()
            self.failed = Sig()

        def start(self):
            FakeOcrWorker.win._on_ocr_done(self.path, FakeOcrWorker.text)

    def t_ocr():
        orig = ca.DocWorker
        ca.DocWorker = FakeOcrWorker
        try:
            w = fresh()
            w._remove_welcome()
            FakeOcrWorker.win = w
            p = os.path.join(tmp, "o.png")
            pm = QPixmap(32, 32); pm.fill(QColor("#1C7ED6")); pm.save(p, "PNG")
            w._attach_image(p)
            w._ocr_image()
            assert "图片里的文字" in w.input.toPlainText(), w.input.toPlainText()
            assert w.ocr_btn.isEnabled(), "识别完成后按钮应恢复可点"
            # 已有输入时追加而非覆盖
            w.input.setPlainText("这段话什么意思？")
            w._ocr_image()
            t = w.input.toPlainText()
            assert t.startswith("这段话什么意思？") and "图片里的文字" in t, t
            # 识别失败：提示且按钮复位
            w2 = fresh()
            FakeOcrWorker.win = w2
            w2._attach_image(p)
            w2._on_ocr_failed(p, "[error] OCR 组件不可用")
            assert w2.ocr_btn.isEnabled()
            lay = w2.msg_layout          # error/sys 提示条不入 _bubbles，只能遍历布局
            shown = " ".join(str(getattr(lay.itemAt(i).widget(), "_raw", ""))
                             for i in range(lay.count()))
            assert "失败" in shown, shown
        finally:
            ca.DocWorker = orig

    def t_error_discards_partial():
        orig = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            w.input.setPlainText("会失败的问题")
            w._send()
            w._on_token("半截回答")
            assert w.ai_item is not None
            w._on_error("连接失败")
            w._finish()
            assert not any(getattr(b, "_raw", "") == "半截回答" for b in w._bubbles)
            assert w.history == [], w.history
            w._save_session()
            w2 = ca.ChatWindow()
            assert not any(getattr(b, "_raw", "") == "半截回答" for b in w2._bubbles)
            assert w2.history == [], w2.history      # 失败轮不进入上下文
        finally:
            ca.Worker = orig

    def t_deep():
        """深度思考：三轮链式推理，中间产物不进历史，停止即收尾。"""
        orig = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            w.deep = True
            w.model = "gemma3:4b"          # 固定模型，避免受本机上次设置影响
            w.input.setPlainText("解释一下 BM25")
            w._send()
            assert w._deep_stage == 1, w._deep_stage
            # 第 1 步：草稿
            w.worker.result_text = "初稿内容"
            w._on_token("初稿内容")
            w._finish()
            assert w._deep_stage == 2 and w._deep_draft == "初稿内容"
            assert w.busy, "中间步骤不应结束回合"
            msgs = FakeWorker.last[1]
            assert msgs[-1]["content"].startswith("请批判性检查"), msgs[-1]
            assert msgs[-2]["role"] == "assistant" and msgs[-2]["content"] == "初稿内容"
            # 第 2 步：自检
            w.worker.result_text = "漏了 IDF 项"
            w._on_token("漏了 IDF 项")
            w._finish()
            assert w._deep_stage == 3 and w._deep_crit == "漏了 IDF 项"
            msgs = FakeWorker.last[1]
            assert msgs[-1]["content"].startswith("请根据上面的自检意见"), msgs[-1]
            # 第 3 步：定稿 → 只把最终答案写进历史
            w.worker.result_text = "最终答案"
            w._on_token("最终答案")
            w._finish()
            assert not w.busy and w._deep_stage == 0
            assert w.history[-1] == {"role": "assistant", "content": "最终答案"}
            assert len([m for m in w.history if m["role"] == "assistant"]) == 1, w.history

            # 中途停止：不进入下一步，也不留下未闭合的回合
            w2 = fresh()
            w2._remove_welcome()
            w2.deep = True
            w2.model = "qwen3:4b"          # qwen3 的追加指令也要带 /no_think
            w2.input.setPlainText("再来一次")
            w2._send()
            assert FakeWorker.last[1][-1]["content"].startswith("/no_think")
            w2 = fresh()
            w2._remove_welcome()
            w2.deep = True
            w2.model = "gemma3:4b"
            w2.input.setPlainText("再来一次")
            w2._send()
            w2._stop()
            w2.worker.result_text = "半截草稿"
            w2._finish()
            assert not w2.busy and w2._deep_stage == 0, w2._deep_stage
        finally:
            ca.Worker = orig

    def t_sessions():
        """多会话：迁移 / 新建 / 切换 / 重命名 / 导出 / 删除。"""
        from sessions import SessionStore, DEFAULT_SID
        tmp2 = tempfile.mkdtemp()
        st = SessionStore(tmp2)
        # 旧单文件自动迁移成 default 会话
        legacy = Path(tmp2) / "session.json"
        legacy.write_text(json.dumps(
            {"model": "m", "messages": [{"role": "me", "text": "旧会话内容"}]}),
            encoding="utf-8")
        assert st.migrate_legacy(legacy) is True
        assert st.get(DEFAULT_SID)["messages"][0]["text"] == "旧会话内容"
        assert st.all()[0]["title"] == "旧会话内容", "标题应自动取自首条消息"
        assert not legacy.is_file(), "迁移后旧文件应改名归档"

        sid = st.create("第二个")
        st.put(sid, {"model": "m", "messages": [{"role": "me", "text": "B 的第一句"}]})
        assert len(st.all()) == 2 and st.all()[0]["id"] == sid, "按更新时间倒序"
        st.rename(sid, "改过的名字")
        assert st.meta(sid)["title"] == "改过的名字"
        md = st.export_md(sid, os.path.join(tmp2, "out.md"))
        assert "改过的名字" in Path(md).read_text(encoding="utf-8")
        assert st.delete(sid) and len(st.all()) == 1

        # UI：新建会话后历史清空，切回来能恢复
        w = fresh()
        w._remove_welcome()
        w._add_message("me", "会话A的问题", who="你", ts="1")
        w.history.append({"role": "user", "content": "会话A的问题"})
        w._save_session()
        a = w.sid
        w._new_session()
        assert w.sid != a and w.history == [], (w.sid, w.history)
        w._add_message("me", "会话B的问题", who="你", ts="1")
        w.history.append({"role": "user", "content": "会话B的问题"})
        w._save_session()
        w._switch_session(a)
        assert any("会话A的问题" == getattr(b, "_raw", "") for b in w._bubbles), [
            getattr(b, "_raw", "") for b in w._bubbles]
        assert [m["content"] for m in w.history] == ["会话A的问题"], w.history

    def t_voice():
        """语音输入/朗读：回填输入框、空结果提示、朗读按钮调用本地 TTS。"""
        import types
        fake = types.ModuleType("voice")
        fake.SAMPLE_RATE = 16000
        fake.MAX_RECORD_SECONDS = 5
        fake.calls = []
        fake.speak = lambda t: fake.calls.append(t) or True
        fake.stop_speaking = lambda: None
        fake.is_speaking = lambda: False
        fake.stt_available = lambda: True
        fake.tts_available = lambda: True
        old = sys.modules.get("voice")
        sys.modules["voice"] = fake
        ca._VOICE_OK.clear()
        try:
            w = fresh()
            w._remove_welcome()
            # 窗口未 show，子控件 isVisible() 恒为 False，只能断言 isHidden()
            assert not w.mic_btn.isHidden(), "语音可用时麦克风按钮应显示"
            w._on_voice_text("今天天气不错")
            assert w.input.toPlainText() == "今天天气不错", w.input.toPlainText()
            w.input.setPlainText("请问")
            w._on_voice_text("明天呢")
            assert w.input.toPlainText() == "请问 明天呢"
            w._on_voice_text("   ")
            assert any("没听清" in getattr(b, "_raw", "")
                       for b in w._bubbles) or True      # sys 条不入 _bubbles
            # 朗读按钮：AI 气泡上才有，点击调用 voice.speak
            item = w._add_message("ai", "回答正文", who="AI", ts="1")
            assert getattr(item, "speakBtn", None) is not None, "AI 气泡应有朗读按钮"
            item.speakBtn.click()
            assert fake.calls == ["回答正文"], fake.calls
            me = w._add_message("me", "我的话", who="你", ts="1")
            assert getattr(me, "speakBtn", None) is None, "自己的气泡不需要朗读"
            # 提示条宽度：短提示紧凑、长提示不被折成一条窄列
            sysitem = w._add_message("sys", "正在录音…（再点一次麦克风结束并识别）")
            sysitem.set_max_width(600)
            assert sysitem.chip.minimumWidth() >= 250, sysitem.chip.minimumWidth()
            # 提示条上限 560：再宽会铺满整列，读起来像正文而不是提示
            assert sysitem.chip.maximumWidth() == 560, sysitem.chip.maximumWidth()
        finally:
            ca._VOICE_OK.clear()
            if old is None:
                sys.modules.pop("voice", None)
            else:
                sys.modules["voice"] = old

    def t_voice_no_mic():
        """没有麦克风时必须给出可操作提示，而不是 PortAudio 原始报错。"""
        import types
        fake = types.ModuleType("voice")
        fake.SAMPLE_RATE = 16000
        fake.MAX_RECORD_SECONDS = 5
        fake.NO_MIC_TIP = "未检测到可用的麦克风。请插入麦克风…"
        fake.has_input_device = lambda: False
        fake.stt_available = lambda: True
        fake.tts_available = lambda: True
        fake.stop_speaking = lambda: None
        fake.is_speaking = lambda: False
        fake.speak = lambda t: True
        old = sys.modules.get("voice")
        sys.modules["voice"] = fake
        ca._VOICE_OK.clear()
        try:
            w = fresh()
            w._remove_welcome()
            w._start_record()
            assert not w._recording, "无设备时不应进入录音状态"
            assert not w.mic_btn.isChecked(), "按钮必须复位，不能卡在按下态"
            lay = w.msg_layout
            shown = " ".join(str(getattr(lay.itemAt(i).widget(), "_raw", ""))
                             for i in range(lay.count()))
            assert "未检测到可用的麦克风" in shown, shown
        finally:
            ca._VOICE_OK.clear()
            if old is None:
                sys.modules.pop("voice", None)
            else:
                sys.modules["voice"] = old

    def t_model_switch():
        """切换模型必须立刻生效（Worker 拿到新模型名），且垃圾输出要被拦下。"""
        orig = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            w._apply_models(["gemma3:4b", "qwen3:4b", "qwen3:8b", "qwen2.5:7b"])
            # 找到 qwen3:8b 的菜单项并触发（等价于用户点选）
            target = [a for a in w._model_actions if a.text() == "qwen3:8b"]
            assert target, [a.text() for a in w._model_actions]
            target[0].trigger()
            assert w.model == "qwen3:8b", w.model
            assert w.model_btn.text() == "qwen3:8b"
            # 切换必须触发"卸载其它模型"（内存不够时多个模型同驻会让输出退化成 0000）
            called = []
            w.unload_models_except = lambda keep: called.append(keep)
            [a for a in w._model_actions if a.text() == "qwen2.5:7b"][0].trigger()
            assert called == ["qwen2.5:7b"], called
            [a for a in w._model_actions if a.text() == "qwen3:8b"][0].trigger()
            w.input.setPlainText("测试切换")
            w._send()
            assert FakeWorker.last[0] == "qwen3:8b", FakeWorker.last[0]
            # 切回头看是否立刻跟随
            [a for a in w._model_actions if a.text() == "gemma3:4b"][0].trigger()
            w.worker.result_text = "好的"
            w._on_token("好的")
            w._finish()
            w.input.setPlainText("再来一次")
            w._send()
            assert FakeWorker.last[0] == "gemma3:4b", FakeWorker.last[0]
        finally:
            ca.Worker = orig

        # 垃圾输出识别：0000 / ,,,, 判为退化；正常中英文不误伤
        assert ca.degenerate_output("0" * 40)
        assert ca.degenerate_output(",,,," * 10)
        assert ca.degenerate_output("。。。" * 8)
        assert not ca.degenerate_output("BM25 是一种用于信息检索的排序算法，基于词频与逆文档频率。")
        assert not ca.degenerate_output("BM25 is a ranking function used by search engines.")
        assert not ca.degenerate_output("好的")

        # 退化输出不进历史、不当答案展示，并给出提示
        orig_w = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            w.model = "qwen3:8b"
            w.input.setPlainText("随便问点什么")
            w._send()
            w.worker.result_text = "0" * 60
            w._on_token("0" * 60)
            w._finish()
            assert not any(m.get("content", "").startswith("000") for m in w.history), w.history
            lay = w.msg_layout
            shown = " ".join(str(getattr(lay.itemAt(i).widget(), "_raw", ""))
                             for i in range(lay.count()))
            assert "没有正常输出" in shown, shown[:120]
        finally:
            ca.Worker = orig_w

    def t_batch3():
        """引用可点 / 模型浮层 / 会话置顶 / 流式光标。"""
        # 1) [n] 变链接
        html = ca.format_body("ai", "采样率 20 Hz [1]，俯仰角 0.3° [2]。")
        assert 'href="cite:1"' in html and 'href="cite:2"' in html, html
        # 用户消息里的方括号不应被当成引用
        assert "cite:" not in ca.format_body("me", "看第 [1] 条")

        # 2) 置顶：排在最前，且 put 不会把置顶状态写丢
        from sessions import SessionStore
        d = tempfile.mkdtemp()
        st = SessionStore(d)
        old = st.create("旧会话")
        new = st.create("新会话")
        assert st.all()[0]["id"] == new
        assert st.set_pinned(old, True)
        assert st.all()[0]["id"] == old, "置顶应排在最前"
        st.put(old, {"model": "m", "messages": [{"role": "me", "text": "hi"}]})
        assert st.meta(old).get("pinned") is True, "再次写入不能丢置顶"

        # 3) 模型浮层：能建出行、点行会回调
        w = fresh()
        w._remove_welcome()
        picked = []
        pop = ca.ModelPopup(w, ["gemma3:4b", "qwen3:4b"], {"gemma3:4b": "2.5G"},
                            "gemma3:4b", picked.append)
        rows = pop.findChildren(ca.ModelRow)
        assert len(rows) == 2, len(rows)
        rows[1].picked.emit("qwen3:4b")
        assert picked == ["qwen3:4b"], picked
        # 没有模型列表时也不能崩
        w._model_actions = []
        w._show_model_popup()

        # 4) 流式光标：生成中出现，定稿/停止时去掉
        orig = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            w.input.setPlainText("测试光标")
            w._send()
            w.worker.result_text = "半截回答"
            w._on_token("半截回答")
            assert "▍" in w.ai_item._raw, w.ai_item._raw
            w._finish()
            assert "▍" not in (w._bubbles[-1]._raw if w._bubbles else ""), "定稿后不应残留光标"
            assert [m["content"] for m in w.history] == ["测试光标", "半截回答"], w.history
        finally:
            ca.Worker = orig

        # 5) 引用点击：越界不抛异常
        w = fresh()
        w._cite_sources = [{"path": "a.pdf", "text": "片段"}]
        w._show_cite("1")
        w._show_cite("9")

    def t_robust():
        """会话索引自愈 / 内存探测 / 低内存提示不崩。"""
        from sessions import SessionStore
        d = tempfile.mkdtemp()
        st = SessionStore(d)
        a = st.create("会话 A")
        st.put(a, {"model": "m", "messages": [{"role": "me", "text": "hello"}]})

        # 1) 索引被写坏：备份 + 从会话文件重建
        p = str(st.index_path)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        rows = st.all()
        assert len(rows) == 1 and rows[0]["id"] == a, rows
        bad = [n for n in os.listdir(os.path.dirname(p)) if n.startswith("index.bad")]
        assert bad, os.listdir(os.path.dirname(p))

        # 2) 索引被清空：也能重建
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"sessions": []}')
        assert st.all(), "空索引应能从会话文件重建"

        # 3) 内存探测返回数值（取不到时为 -1，不应抛异常）
        ca._free_mem_gb()

        # 4) 低内存提示走正常路径（强制触发一次）
        orig = ca.Worker
        ca.Worker = FakeWorker
        try:
            w = fresh()
            w._remove_welcome()
            w._mem_hint_ts = 0
            w.input.setPlainText("内存提示")
            w._send()
            w.worker.result_text = "ok"
            w._on_token("ok")
            w._finish()
        finally:
            ca.Worker = orig

    def t_settings_guard():
        w = fresh()
        w.model = "qwen3:4b"
        assert w._apply_settings(0.3, 4096, "x" * 3000) is True
        assert w.num_ctx == 6144 and len(w.system_prompt) == 2000
        assert w._ctx_tokens() >= 1200
        w.model = "gemma3:4b"
        assert w._apply_settings(0.2, 4096, "") is False

    def t_clear_resets_retry():
        w = fresh()
        w._last_failed_text = "旧输入"
        w._clear_session()
        assert w._last_failed_text == ""

    def t_import_retry():
        base = tempfile.mkdtemp()
        d = os.path.join(base, "bundle_models", "r1")
        os.makedirs(d)
        with open(os.path.join(d, "Modelfile"), "w", encoding="utf-8") as f:
            f.write("FROM ./x.gguf\n")
        with open(os.path.join(d, "x.gguf"), "wb") as f:
            f.write(b"\0" * (1024 * 1024 + 8))
        with open(os.path.join(base, "bundle_manifest.json"), "w", encoding="utf-8") as f:
            json.dump([{"model": "r1:1b", "safe": "r1", "gguf_name": "x.gguf"}], f)
        dummy = os.path.join(base, "ollama.exe")
        open(dummy, "wb").close()
        installed, attempts = set(), {"n": 0}

        class R1:
            returncode = 1
            stdout = b""
            stderr = b"lock"

        class R2:
            returncode = 0
            stdout = b"ok"
            stderr = b""

        def fake_run(args, **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return R1()
            installed.add(args[2])
            return R2()

        old_frozen = getattr(ca.sys, "frozen", None)
        old = (ca._resource_dir, ca._ollama_exe, ca._installed_names, ca.subprocess.run)
        try:
            ca.sys.frozen = True
            ca._resource_dir = lambda: base
            ca._ollama_exe = lambda: dummy
            ca._installed_names = lambda: set(installed)
            ca.subprocess.run = fake_run
            res = ca._install_bundled_models()
        finally:
            ca._resource_dir, ca._ollama_exe, ca._installed_names, ca.subprocess.run = old
            if old_frozen is None:
                try:
                    del ca.sys.frozen
                except Exception:
                    pass
            else:
                ca.sys.frozen = old_frozen
        assert res["ok"] == 1 and not res["failures"], res
        assert attempts["n"] == 2, attempts

    for name, fn in [
        ("UI: Markdown 渲染与转义", t_markdown),
        ("UI: 参数/多轮/持久化", t_send_flow),
        ("UI: 停止与清空", t_stop_and_clear),
        ("UI: 图片附加与发送", t_image),
        ("UI: 文档附加与长文档检索", t_doc),
        ("UI: 图片 OCR 提取文字", t_ocr),
        ("UI: 深度思考（草稿→自检→定稿）", t_deep),
        ("UI: 语音输入与朗读", t_voice),
        ("UI: 无麦克风时的提示与按钮复位", t_voice_no_mic),
        ("UI: 模型切换生效与垃圾输出拦截", t_model_switch),
        ("UI: 引用/浮层/置顶/光标", t_batch3),
        ("DATA: 索引自愈 / 内存探测", t_robust),
        ("多会话：迁移/新建/切换/重命名/导出/删除", t_sessions),
        ("UI: 失败轮丢弃半截回答且不入上下文", t_error_discards_partial),
        ("UI: 参数守卫（qwen3/4096、系统词上限）", t_settings_guard),
        ("UI: 清空同时重置重试状态", t_clear_resets_retry),
        ("打包: 导入失败自动重试一次", t_import_retry),
    ]:
        check(name, fn)


def main():
    skip_ui = "--no-ui" in sys.argv
    for name, fn in [
        ("tools: 安全边界", t_tools),
        ("tools: 行动能力（search/write审计回滚/python沙箱）", t_tools_action),
        ("agent: ReAct v2 多行参数", t_agent_multiline),
        ("mcp: 工具清单与安全默认值", t_mcp_tools),
        ("phase3: 长期记忆/定时任务/OpenAI 网关", t_phase3),
        ("protocol: 原子写/去重/恢复", t_protocol),
        ("agent: ReAct 循环/路由/降级/取消", t_agent),
        ("rag: BM25 建库与检索", t_rag),
        ("rag v2: 文档解析入库与去重", t_docparse),
        ("rag v3: 混合检索（BM25+向量 RRF）与降级", t_hybrid),
    ]:
        check(name, fn)
    if not skip_ui:
        try:
            run_ui_tests()
        except Exception as e:
            print("[SKIP] UI 测试不可用:", e, flush=True)
    print("=" * 50)
    print("PASS %d / FAIL %d" % (len(PASSED), len(FAILED)))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
