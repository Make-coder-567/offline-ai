"""MCP server：把本机 Ollama 模型暴露为 WorkBuddy 可调用的工具（离线推理）。

WorkBuddy 通过 ~/.workbuddy/mcp.json 以 stdio 启动本文件后，
即可在对话中调用 local_llm_chat 等工具 —— 推理完全走本地，断网可用。
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))
from paths import resolve_workspace  # noqa: E402

import yaml
try:
    from mcp.server.fastmcp import FastMCP, Context
except ImportError:                       # 老版本 SDK 无 Context，降级为无进度上报
    from mcp.server.fastmcp import FastMCP
    Context = None

try:
    import ollama
except ImportError:
    ollama = None

cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
# ollama 连接一律走 127.0.0.1：localhost 会触发 IPv6(::1) 回退, 每次请求多花 ~2s
os.environ["OLLAMA_HOST"] = str(
    cfg.get("ollama_base") or "http://127.0.0.1:11434"
).replace("localhost", "127.0.0.1")
mcp = FastMCP("offline-ai")


MAX_PROMPT_CHARS = 32000  # 超长 prompt 会把 CPU prefill 拖到数分钟并吃满内存，直接拒绝


def _adapt_prompt(model: str, prompt: str) -> str:
    """qwen3 系默认 thinking 模式会让 content 恒为空（输出全进 thinking），
    并可能触发 repeat-limit 中止；`/no_think` 为其内置软开关（已实测有效）。"""
    if str(model).lower().startswith("qwen3"):
        return "/no_think\n" + prompt
    return prompt


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)


def _clean_response(text: str) -> str:
    """去掉 /api/generate 混入 response 的 <think>...</think> 块；清空则退回原文。"""
    cleaned = _THINK_BLOCK.sub("", text or "").strip()
    return cleaned or (text or "").strip()


def _unload(cfg: dict, keep: str):
    try:
        from server.ollama_guard import unload_others_async
        unload_others_async(cfg.get("ollama_base") or "http://127.0.0.1:11434", keep)
    except Exception:
        pass


def _gen(prompt: str, temperature: float = 0.3, model: str = None,
         ctx: "Context" = None) -> str:
    if ollama is None:
        return "[error] ollama python lib not installed; run: pip install ollama"
    if len(prompt) > MAX_PROMPT_CHARS:
        return f"[error] prompt too large: {len(prompt)} > {MAX_PROMPT_CHARS} chars"
    model = model or cfg["model"]
    _unload(cfg, model)
    options = {"temperature": temperature,
               "num_ctx": int(cfg.get("num_ctx", 6144)),
               "keep_alive": cfg.get("keep_alive", "10m")}
    try:
        if ctx is None:
            r = ollama.generate(model=model,
                                prompt=_adapt_prompt(model, prompt), options=options)
            text = _clean_response(r["response"])
        else:
            # 流式：MCP 协议不支持工具结果流式返回，但可上报"已生成 token 数"进度
            parts, n = [], 0
            for chunk in ollama.generate(model=model,
                                         prompt=_adapt_prompt(model, prompt),
                                         options=options, stream=True):
                tok = chunk.get("response", "") if hasattr(chunk, "get") else ""
                if tok:
                    parts.append(tok)
                    n += 1
                    if n % 20 == 0:
                        try:
                            ctx.report_progress(n, None)
                        except Exception:
                            pass
            text = _clean_response("".join(parts))
        return text if text.strip() else "（模型未返回内容，请重试或换用其他模型）"
    except Exception as e:
        return _friendly_err(str(e))


def _route(kind: str, fallback: str) -> str:
    return (cfg.get("routes") or {}).get(kind, fallback)


# 常见环境错误的可操作提示（原始错误仍附在末尾便于排查）
_ERR_HINTS = {
    "does not support embeddings":
        "本机 Ollama 构建未启用 embeddings（该版本 CLI 也没有 --embeddings 参数）。"
        "可改用支持 embeddings 的 Ollama 发行版，或在应用侧用本地向量库替代。",
    "does not support multimodal":
        "当前模型不支持图像输入：内置 GGUF 为纯文本版，缺少 mmproj 视觉投影文件。"
        "需换成带视觉能力的模型（例如官方 gemma3:4b 完整版）后重试。",
}


def _friendly_err(err: str) -> str:
    for key, hint in _ERR_HINTS.items():
        if key in err:
            return f"[error] {hint}\n原始错误：{err}"
    return f"[error] {err}"


_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_DOC_BYTES = 50 * 1024 * 1024


def _in_workspace(path: str) -> Path:
    """把路径限制在 workspace 内，防越权读取任意文件。"""
    ws = Path(resolve_workspace(cfg))
    p = Path(path)
    p = (ws / p).resolve() if not p.is_absolute() else p.resolve()
    if not p.is_relative_to(ws):
        raise ValueError("path escapes workspace")
    return p


def _safe_image(path: str) -> Path:
    """图片路径限制在配置的 workspace 内，防越权读取任意文件。"""
    p = _in_workspace(path)
    if not p.is_file():
        raise ValueError(f"image not found: {path}")
    if p.stat().st_size > _MAX_IMAGE_BYTES:
        raise ValueError("image too large (max 20MB)")
    return p


def _safe_doc(path: str) -> Path:
    """文档路径同样限制在 workspace 内（且必须是可解析类型）。"""
    p = _in_workspace(path)
    if not p.is_file():
        raise ValueError(f"file not found: {path}")
    if p.stat().st_size > _MAX_DOC_BYTES:
        raise ValueError("file too large (max 50MB)")
    try:
        from server import docparse
    except Exception:
        import docparse  # type: ignore
    if not docparse.supported(p):
        raise ValueError(f"unsupported file type: {p.suffix or '(无后缀)'}；"
                         f"支持 {', '.join(sorted(docparse.SUPPORTED_EXTS | docparse.OCR_EXTS))}")
    return p


_DEEP_CRITIQUE = ("请批判性检查上面这份回答：指出事实错误、遗漏的关键点、逻辑或表述问题，"
                  "用 3-5 条简短要点列出。不要重写全文，只列问题。")
_DEEP_REVISE = ("请根据上面的自检意见重写最终答案：修正问题、补齐遗漏、保持简洁。"
                "只输出最终答案正文，不要出现「根据意见」「修改说明」这类过程性文字。")


def _gen_deep(prompt: str, temperature: float = 0.3, model: str = None,
              ctx: "Context" = None) -> str:
    """深度思考：草稿 → 自检挑刺 → 定稿（三轮本地推理，耗时约 3 倍）。"""
    draft = _gen(prompt, temperature=min(1.0, temperature + 0.2), model=model, ctx=ctx)
    if draft.startswith("[error]"):
        return draft
    crit = _gen("%s\n\n---\n待检查的回答：\n%s" % (_DEEP_CRITIQUE, draft),
                temperature=0.2, model=model)
    if crit.startswith("[error]"):
        return draft + "\n\n（自检失败，以上为初稿）"
    final = _gen("%s\n\n原问题：%s\n\n初稿：%s\n\n自检意见：%s"
                 % (_DEEP_REVISE, prompt[:1500], draft[:3000], crit[:1500]),
                 temperature=temperature, model=model, ctx=ctx)
    if final.startswith("[error]"):
        return draft + "\n\n（重写失败，以上为初稿）"
    return final


@mcp.tool()
def local_llm_chat(prompt: str, deep: bool = False, ctx: "Context" = None) -> str:
    """调用本机离线大模型(Ollama)进行对话/文本生成。适用于隐私数据或断网场景。

    deep=True 走深度思考（草稿→自检→定稿），质量更好但耗时约 3 倍。
    长任务会通过 MCP 进度通知上报已生成 token 数（协议不支持结果流式返回）。
    """
    if deep:
        return _gen_deep(prompt, ctx=ctx)
    return _gen(prompt, ctx=ctx)


@mcp.tool()
def local_llm_summarize(text: str, max_words: int = 200) -> str:
    """用本地模型对长文本做中文摘要，文本不出本机。"""
    # 截到 4000 字符：默认 num_ctx=2048 时 12000 字符必被静默截断，摘要质量随机受损
    p = f"请用不超过{max_words}字概括以下内容，中文输出：\n\n{text[:4000]}"
    return _gen(p, temperature=0.2)


@mcp.tool()
def local_llm_structured(prompt: str, format_hint: str = "") -> str:
    """用本地模型按要求生成结构化文本（表格/代码/JSON），返回纯文本结果。"""
    p = f"{prompt}\n请只输出结果内容，不要解释。"
    if format_hint:
        p += f"\n格式要求: {format_hint}"
    return _gen(p, temperature=0.2)


@mcp.tool()
def local_model_list() -> str:
    """列出本机已安装的离线模型（名称 + 体积），用于选择合适的模型或排查缺失。"""
    # 直接查 HTTP：不同版本的 ollama 包 API 不一致（旧版 list()、新版 Client.list()），
    # 只有 /api/tags 是稳定的
    try:
        import urllib.request
        base = (cfg.get("ollama_base") or "http://127.0.0.1:11434").rstrip("/")
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(base + "/api/tags", timeout=5) as resp:
            data = json.load(resp)
        rows = ["%s  %.2f GB" % (m.get("name"), (m.get("size") or 0) / 1e9)
                for m in data.get("models", [])]
        return "\n".join(rows) or "(无模型)"
    except Exception as e:
        return f"[error] ollama: {e}"


@mcp.tool()
def local_llm_code(prompt: str) -> str:
    """用代码专用模型(qwen2.5-coder)生成或解释代码，适合函数/脚本/报错分析。"""
    return _gen(prompt, temperature=0.15, model=_route("code", "qwen2.5-coder:3b"))


@mcp.tool()
def local_llm_embed(text: str) -> str:
    """把文本转成本地向量（JSON 数组），供语义检索/RAG 使用；数据不出本机。

    优先用内置 bge-small-zh-v1.5（ONNX，离线可用）；
    该组件缺失时退回 Ollama embeddings（本机构建可能未开放该接口）。
    """
    if len(text) > 8000:
        return "[error] text too large: max 8000 chars"
    try:
        from server import embed as emb
    except Exception:
        emb = None
    if emb is not None and emb.available():
        v = emb.embed_one(text[:2000])
        if v is not None:
            return json.dumps([round(float(x), 5) for x in v], ensure_ascii=False)
    if ollama is None:
        return ("[error] 本地向量模型不可用（%s），且未安装 ollama python lib"
                % (emb.unavailable_reason() if emb else "server.embed 缺失"))
    try:
        r = ollama.embeddings(model=cfg.get("embed_model") or cfg["model"], prompt=text)
        return json.dumps(r.get("embedding", []), ensure_ascii=False)
    except Exception as e:
        return _friendly_err(str(e))


@mcp.tool()
def local_run_python(code: str, timeout: int = 10) -> str:
    """在本机沙箱里执行一段"纯计算"Python（不能读写文件、不能联网），用于算数/数据处理。

    安全边界：AST 检查禁止危险 import（os/sys/subprocess/socket/…）与 open/eval/exec，
    子进程用 -I 隔离并超时强杀，全部调用写入 .offline-audit 审计日志。
    默认关闭——需在 config.yaml 设置 agent.allow_python: true 才会真正执行。
    """
    try:
        from server.tools import Tools
        from server.audit import Audit
    except Exception as e:
        return f"[error] {e}"
    ws = resolve_workspace(cfg)
    allow = bool((cfg.get("agent") or {}).get("allow_python", False))
    return Tools(ws, allow_python=allow, audit=Audit(ws)).run_python(code, timeout=timeout)


@mcp.tool()
def local_audit(n: int = 20) -> str:
    """查看智能体对工作区的写操作/代码执行审计记录（最近 n 条，含是否可回滚）。"""
    try:
        from server.audit import Audit
    except Exception as e:
        return f"[error] {e}"
    rows = Audit(resolve_workspace(cfg)).tail(n)
    if not rows:
        return "（暂无审计记录）"
    return "\n".join(
        "%s %-10s %s%s %s" % (r.get("ts", "-"), r.get("action", "?"),
                              r.get("path", ""), " [可回滚]" if r.get("snap") else "",
                              (r.get("detail") or "")[:60])
        for r in rows)


@mcp.tool()
def local_undo() -> str:
    """撤销智能体最近一次写文件操作（从 .offline-audit 快照恢复；新建文件则删除）。"""
    try:
        from server.audit import Audit
    except Exception as e:
        return f"[error] {e}"
    return Audit(resolve_workspace(cfg)).rollback()


@mcp.tool()
def local_llm_vision(image_path: str, prompt: str = "请用中文描述这张图片的内容。") -> str:
    """用本地多模态模型(gemma3)理解图片。image_path 必须在配置的 workspace 目录内。"""
    if ollama is None:
        return "[error] ollama python lib not installed"
    try:
        p = _safe_image(image_path)
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        _vision_model = _route("vision", "gemma3:4b")
        _unload(cfg, _vision_model)
        r = ollama.chat(
            model=_vision_model,
            messages=[{"role": "user", "content": prompt, "images": [b64]}],
            options={"temperature": 0.2,
                     "keep_alive": cfg.get("keep_alive", "10m")},
        )
        msg = r.get("message", {}) or {}
        return _clean_response(msg.get("content") or msg.get("thinking") or "")
    except Exception as e:
        return _friendly_err(str(e))


# ==================== 本地 RAG（BM25，无需 embeddings） ====================
_RAG = None


def _rag():
    global _RAG
    if _RAG is None:
        from rag import RagIndex
        _RAG = RagIndex(resolve_workspace(cfg))
    return _RAG


@mcp.tool()
def local_rag_index(force: bool = False, ocr: bool = True) -> str:
    """为 workspace 内文件建立/更新本地检索索引（BM25，纯本地，无需 embeddings）。

    除文本/代码外，PDF/Word/Excel/PPT/ePub 与图片（OCR）也会入库。
    ocr=False 可跳过图片与扫描件（建库更快）。
    """
    try:
        st = _rag().build(force=force, use_ocr=ocr)
        head = "索引完成" if st.get("indexed") else "索引已是最新"
        msg = "%s：%d 个文件 / %d 个片段（构建于 %s）" % (
            head, st.get("files", 0), st.get("chunks", 0), st.get("built_at", "-"))
        try:
            st_all = _rag().stats()
            by_vecs = st_all.get("vectors", 0)
            hybrid_ok = bool(st_all.get("hybrid"))
            by = st_all.get("by_type") or {}
            msg += "\n类型：文本 %d / 文档 %d / 图片 %d（已向量化 %d 段，语义检索%s）" % (
                by.get("text", 0), by.get("doc", 0), by.get("image", 0),
                by_vecs, "可用" if hybrid_ok else "不可用，已退回 BM25")
        except Exception:
            pass
        if st.get("skipped"):
            msg += "\n跳过：%s" % ", ".join(st["skipped"][:5])
        return msg
    except Exception as e:
        return f"[error] {e}"


@mcp.tool()
def local_doc_text(path: str, max_chars: int = 6000) -> str:
    """把单个本地文档（PDF/Word/Excel/PPT/ePub/图片）解析为纯文本，便于快速查看或二次处理。

    path 必须在 workspace 内；扫描版 PDF/图片会自动走本地 OCR。
    """
    try:
        p = _safe_doc(path)
        from server import docparse
    except Exception as e:
        return f"[error] {e}"
    text = docparse.extract_text(p)
    if text.startswith("[error]"):
        return text
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n…（已截断，共 {len(text)} 字符）"
    return f"# {p.name}\n\n{text}"


@mcp.tool()
def local_ocr(path: str) -> str:
    """用本地 OCR（RapidOCR / ONNX）识别图片或扫描版 PDF 中的文字，数据不出本机。

    path 必须在 workspace 内；多页 PDF 最多识别前 10 页。
    """
    try:
        p = _safe_doc(path)
        from server import docparse
    except Exception as e:
        return f"[error] {e}"
    text = docparse.extract_text(p, ocr_fallback=True)
    if text.startswith("[error]"):
        return text
    return f"# {p.name}（OCR）\n\n{text}"


@mcp.tool()
def local_doc_ask(path: str, question: str, top_k: int = 3, ctx: "Context" = None) -> str:
    """针对单个本地文档提问：解析 → 单文件入库 → 检索 → 本地模型作答（附 [1][2] 引用）。

    适合长文档问答（几十页 PDF / 大型 Excel），不会把整篇塞进 prompt。
    """
    try:
        p = _safe_doc(path)
    except Exception as e:
        return f"[error] {e}"
    try:
        idx = _rag()
        idx.add_paths([str(p)])
        key = idx.key_for(p)
        hits = idx.query(question, top_k=top_k, paths=[key])
    except Exception as e:
        return f"[error] 检索失败：{e}"

    if not hits:
        # 文档太短/检索不到：直接把全文（截断后）交给模型
        from server import docparse
        full = docparse.extract_text(p)
        if full.startswith("[error]"):
            return full
        prompt = ("下面是文档《%s》的内容，请据此回答问题；"
                  "答案不确定时说明依据不足。\n\n%s\n\n问题：%s"
                  % (p.name, full[:8000], question))
        return _gen(prompt, temperature=0.2, ctx=ctx)

    ctx_text = "\n\n".join("资料%d（%s）：\n%s" % (i, h["path"], h["text"])
                           for i, h in enumerate(hits, 1))
    prompt = ("请只根据下面来自文档《%s》的资料回答问题；资料不足时明确说明。"
              "回答中用 [1][2] 标注引用的资料编号。\n\n%s\n\n问题：%s"
              % (p.name, ctx_text, question))
    ans = _gen(prompt, temperature=0.2, ctx=ctx)
    return ans + "\n\n来源：\n" + "\n".join(
        "[%d] %s" % (i, h["path"]) for i, h in enumerate(hits, 1))


@mcp.tool()
def local_rag_query(question: str, top_k: int = 4, hybrid: bool = True) -> str:
    """在本地索引中检索与问题最相关的片段（返回路径、得分、原文）。

    hybrid=True 时用 BM25 + 本地向量（bge-small-zh）加权 RRF 融合，
    能召回"用词不同但语义相同"的内容；向量不可用时自动退回 BM25。
    """
    try:
        hits = _rag().query(question, top_k=top_k, hybrid=hybrid)
        if not hits:
            return "（无结果：请先调用 local_rag_index 建索引，或换关键词重试）"
        return "\n\n".join("[%d] %s（score %.2f）\n%s"
                           % (i, h["path"], h["score"], h["text"])
                           for i, h in enumerate(hits, 1))
    except Exception as e:
        return f"[error] {e}"


@mcp.tool()
def local_rag_answer(question: str, top_k: int = 3, hybrid: bool = True) -> str:
    """检索本地资料并让本地模型基于资料作答，回答附来源编号（全程离线）。"""
    try:
        hits = _rag().query(question, top_k=top_k, hybrid=hybrid)
        if not hits:
            return "（没有检索到相关资料，请先运行 local_rag_index）"
        ctx_text = "\n\n".join("资料%d（%s）：\n%s" % (i, h["path"], h["text"])
                               for i, h in enumerate(hits, 1))
        prompt = ("请只根据下面的资料回答问题；资料不足时明确说明。"
                  "回答末尾用 [1][2] 标注引用的资料编号。\n\n%s\n\n问题：%s"
                  % (ctx_text, question))
        ans = _gen(prompt, temperature=0.2)
        return ans + "\n\n来源：\n" + "\n".join(
            "[%d] %s" % (i, h["path"]) for i, h in enumerate(hits, 1))
    except Exception as e:
        return f"[error] {e}"


# ==================== 长期记忆 ====================
@mcp.tool()
def local_memory_add(fact: str) -> str:
    """记住一条关于用户的事实/偏好（写入本机 memory.md，之后每次对话可被调用）。

    适合记：称呼、常用语言、项目背景、明确的偏好（如"回答简洁些"）。
    """
    from server.memory import remember
    from server.paths import data_dir
    return remember(data_dir(), fact)


@mcp.tool()
def local_memory_search(query: str = "", top_k: int = 8) -> str:
    """检索长期记忆（关键词匹配）；query 留空则列出最近的记忆条目。"""
    from server.memory import recall
    from server.paths import data_dir
    return recall(data_dir(), query, top_k)


@mcp.tool()
def local_memory_forget(keyword: str) -> str:
    """从长期记忆里删除包含该关键词的条目（用户要求"忘掉某事"时用）。"""
    from server.memory import forget
    from server.paths import data_dir
    return forget(data_dir(), keyword)


# ==================== 定时任务 ====================
@mcp.tool()
def local_schedule_add(prompt: str, every_minutes: int = 0, daily_at: str = "",
                       model: str = "") -> str:
    """添加一个定时离线任务：每 N 分钟执行一次，或每天 HH:MM 执行一次。

    需要 server/scheduler.py 常驻才会真正触发；结果写入 <data>/schedule.log。
    """
    try:
        from server.scheduler import add
        from server.paths import data_dir
        item = add(data_dir(), prompt, every_minutes, daily_at, model)
        return "[ok] 已添加任务 %s：%s" % (item["id"], prompt[:60])
    except Exception as e:
        return f"[error] {e}"


@mcp.tool()
def local_schedule_list() -> str:
    """列出已配置的定时任务（含上次执行时间）。"""
    from server.scheduler import list_all
    from server.paths import data_dir
    rows = list_all(data_dir())
    if not rows:
        return "（暂无定时任务）"
    return "\n".join(
        "%s  %s  %s  上次：%s" % (
            r.get("id"), "每%dm" % r["every_minutes"] if r.get("every_minutes")
            else "每天 %s" % r.get("daily_at"),
            (r.get("prompt") or "")[:40],
            time.strftime("%m-%d %H:%M", time.localtime(float(r["last_run"])))
            if r.get("last_run") else "-")
        for r in rows)


@mcp.tool()
def local_schedule_remove(task_id: str) -> str:
    """删除一个定时任务（用 local_schedule_list 查 id）。"""
    from server.scheduler import remove
    from server.paths import data_dir
    return "[ok] 已删除" if remove(data_dir(), task_id) else "[error] 未找到该任务"


if __name__ == "__main__":
    mcp.run()
