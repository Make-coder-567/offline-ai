"""离线AI 智能体（独立运行）：

监听 workspace 内 .offline-bridge/inbox 的任务 JSON，
调用本机 Ollama 模型 + 工具自主处理，结果写回 outbox，并从 inbox 移除。

启动: python agent.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))

import yaml
from bridge.protocol import Bridge, Task
from tools import Tools
from audit import Audit

# ollama 连接一律走 127.0.0.1：localhost 会触发 IPv6(::1) 回退, 每次请求多花 ~2s
os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")

try:
    import ollama
except ImportError:
    ollama = None


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def _adapt_prompt(model: str, prompt: str) -> str:
    """qwen3 系默认开启 thinking 模式：content 恒为空、输出全部进 thinking,
    短任务还会因思考循环触发 Ollama 的 token repeat limit 中止。
    `/no_think` 是 Qwen3 训练时内置的官方软开关（已实测：加前缀后 content 正常）。"""
    if str(model).lower().startswith("qwen3"):
        return "/no_think\n" + prompt
    return prompt


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)


def _clean_response(text: str) -> str:
    """去掉 thinking 模型经 /api/generate 混入 response 的 <think>...</think> 块。
    清理后若为空（只剩思考内容），退回原文——宁可展示思考也不给空气泡。"""
    cleaned = _THINK_BLOCK.sub("", text or "").strip()
    return cleaned or (text or "").strip()


def _unload(cfg: dict, keep: str):
    try:
        from server.ollama_guard import unload_others_async
        unload_others_async(cfg.get("ollama_base") or "http://127.0.0.1:11434", keep)
    except Exception:
        pass


def run_llm(cfg: dict, prompt: str, temperature: float = 0.3,
            model: str = None, num_predict: int = None) -> str:
    if ollama is None:
        return "[error] ollama python lib not installed"
    model = model or cfg["model"]
    # 本机内存有限：先把别的模型卸掉，否则 KV 分配失败会让输出退化成乱码
    _unload(cfg, model)
    options = {"temperature": temperature,
               "num_ctx": int(cfg.get("num_ctx", 6144)),
               "keep_alive": cfg.get("keep_alive", "10m")}
    if num_predict:
        options["num_predict"] = int(num_predict)
    try:
        r = ollama.generate(
            model=model, prompt=_adapt_prompt(model, prompt), options=options)
        return _clean_response(r["response"])
    except Exception as e:
        return f"[error] ollama: {e}"


MAX_TASK_FILES = 10          # 单任务最多读取的文件数（防 prompt 爆炸拖死 CPU prefill）
MAX_TASK_CTX_CHARS = 24000   # 单任务上下文字符上限（CPU 上 prefill 近似 O(n^2)，超长极慢）

# ---------- ReAct 工具调用协议（小模型可用的极简格式） ----------
# write_file / run_python 的参数是多行的（首行路径，其余正文 / 整段代码），
# 所以 Input 的解析要一直吃到下一个 Action/Observation 为止（见 _parse_action）。
MULTILINE_ACTIONS = {"write_file", "run_python"}
SYSTEM_PROMPT = (
    "你是运行在本机的离线AI智能体，可以调用工具完成任务。\n"
    "可用工具：\n"
    "- list_dir   : 列出目录，参数为相对路径（根目录用 .）\n"
    "- read_file  : 读取文本文件，参数为相对路径\n"
    "- search_text: 在工作区里搜索关键词，参数为关键词\n"
    "- write_file : 写文件。Input 第一行是相对路径，其余各行是文件正文\n"
    "- run_python : 执行一段 Python 计算代码（不能读写文件、不能联网）\n"
    "- finish     : 提交最终答案，参数为答案正文\n\n"
    "输出规则：每次只输出 Action 和 Input，不要解释。需要信息就先调用工具，"
    "信息足够后再用 finish 提交。\n"
    "注意：read_file 只能读文件；若 Observation 提示是目录，请先用 list_dir 查看。\n"
    "写文件前通常应先 read_file 确认是否已有内容，避免覆盖用户资料（写操作会留快照，可回滚）。\n"
    "路径一律相对工作区根目录：不要写绝对路径，也不要加项目名前缀。\n"
    "遇到错误不要放弃：换一个路径或改用其他工具继续尝试，直到能用 finish 提交答案。\n"
    "示例：\n"
    "任务: 看看 notes 目录里有什么？report.md 说了什么？\n"
    "Action: list_dir\n"
    "Input: notes\n"
    "Observation: D sub\nF report.md\nAction: read_file\nInput: notes/report.md\n"
    "Observation: # 周报 ...\n"
    "Action: finish\n"
    "Input: notes 目录下有 sub 子目录和 report.md，内容是一份周报。\n"
)
ACTION_RE = re.compile(
    r"^\s*Action\s*[:：]\s*(list_dir|read_file|search_text|write_file|run_python|finish)\b",
    re.I | re.M)


def _strip_fence(arg: str) -> str:
    """模型爱用 ``` 包代码/正文：去掉首尾围栏，保留内容。"""
    a = arg.strip()
    if a.startswith("```"):
        nl = a.find("\n")
        a = a[nl + 1:] if nl > 0 else a[3:]
        if a.rstrip().endswith("```"):
            a = a.rstrip()[:-3]
    return a.strip("\n")


_OBS_RE = re.compile(r"^\s*Observation\s*[:：]", re.M | re.I)
_CONTENT_LABEL_RE = re.compile(r"^\s*(?:Content|正文|内容|文本)\s*[:：]\s*", re.I)
# 任务里"要写到哪个文件"的路径（小模型经常读够了就直接给答案，需要兜底落盘）
_PATH_TOKEN = r"[\w\-./\\]+\.(?:md|txt|json|csv|log|py|ya?ml)"
_TARGET_RE = re.compile(r"(?:写入|写进|写到|写至|保存到|保存为|输出到|生成|创建)\s*[:：]?\s*"
                        r"(" + _PATH_TOKEN + r")", re.I)
_ANY_PATH_RE = re.compile(_PATH_TOKEN, re.I)


def _guess_target(prompt: str) -> str:
    """从任务描述里猜出"要写到哪个文件"；猜不到返回空串。

    优先取"写入/保存到 X"里的 X（目标），而不是任务里先出现的输入文件，
    否则兜底落盘会把结果覆盖到源文件上。
    """
    m = _TARGET_RE.search(prompt or "")
    if m:
        return m.group(1)
    all_paths = _ANY_PATH_RE.findall(prompt or "")
    return all_paths[-1] if all_paths else ""


def _cut_hallucination(text: str) -> str:
    """小模型常把整段 ReAct 对话（含自己编的 Observation）续写出来。

    真实流程里 Observation 只能由我们回填，所以模型输出中一旦出现
    Observation，其后内容全是幻觉——直接截断，避免照着假观察去调工具。
    """
    m = _OBS_RE.search(text or "")
    return (text or "")[:m.start()] if m else (text or "")


def _parse_action(text: str):
    """解析模型输出中的 Action/Input；解析不到返回 (None, None)（视为最终回答）。

    v2：Input 支持多行（write_file 正文 / run_python 代码），
    一直读到下一个 Action/Observation 或文本结束。
    """
    text = _cut_hallucination(text)
    m = ACTION_RE.search(text or "")
    if not m:
        return None, None
    action = m.group(1).lower()
    rest = text[m.end():]
    mi = re.search(r"Input\s*[:：]\s*(.*?)(?=^\s*(?:Action|Observation)\s*[:：]|\Z)",
                   rest, re.I | re.S | re.M)
    arg = (mi.group(1) if mi else "").strip()
    if action in MULTILINE_ACTIONS:
        return action, _strip_fence(arg)
    arg = arg.splitlines()[0].strip().strip("`'\"") if arg else ""
    return action, arg


def _norm_path(arg: str, tools: Tools) -> str:
    """小模型常照抄示例或绝对路径，把工作区名前缀剥掉（"ws/a.md" -> "a.md"）。"""
    a = (arg or "").strip().strip("`'\"").replace("\\", "/")
    try:
        name = tools.ws.name
        if name and a.lower().startswith(name.lower() + "/"):
            a = a[len(name) + 1:]
    except Exception:
        pass
    return a


def _run_action(action: str, arg: str, tools: Tools, task_id: str = "") -> str:
    if action == "list_dir":
        return tools.list_dir(_norm_path(arg, tools) or ".")
    if action == "read_file":
        arg = _norm_path(arg, tools)
        if not arg:
            return "[error] empty path"
        # 目录误当文件读是小模型高频错误，提前给出可操作提示
        try:
            p = (tools.ws / arg).resolve()
            if p.is_dir():
                return f"[error] {arg} 是目录，不是文件。请改用 list_dir 查看其内容。"
        except Exception:
            pass
        res = tools.read_file(arg)
        if res.startswith("[error] file not found"):
            # 找不到文件时回带根目录列表，让模型能自己纠正路径，而不是原地重试到步数耗尽
            res += "\n工作区根目录内容：\n" + tools.list_dir(".")[:400]
        return res
    if action == "search_text":
        if not arg:
            return "[error] empty pattern"
        return tools.search_text(arg)
    if action == "write_file":
        if not arg:
            return "[error] write_file 需要参数：第一行为路径，其余为正文"
        path, _, content = arg.partition("\n")
        path = _norm_path(path, tools)
        if not path:
            return "[error] write_file 缺少文件路径（第一行）"
        if not content.strip():
            return f"[error] write_file 缺少正文（{path}）"
        # 模型爱写 "Content: xxx" 这类标签行，去掉标签再落盘
        content = _CONTENT_LABEL_RE.sub("", content, count=1)
        return tools.write_file(path, content, task_id=task_id)
    if action == "run_python":
        if not arg:
            return "[error] run_python 需要代码"
        return tools.run_python(arg, task_id=task_id)
    return ""


class TaskCancelled(Exception):
    """用户通过 inbox/<task_id>.cancel 标记取消任务。"""


def _cancel_requested(bridge: Bridge, task_id: str) -> bool:
    try:
        return (bridge.inbox_path() / (task_id + ".cancel")).exists()
    except Exception:
        return False


def handle_task(cfg: dict, bridge: Bridge, task: Task, tools: Tools) -> str:
    if len(task.files) > MAX_TASK_FILES:
        return f"[error] too many files: {len(task.files)} (max {MAX_TASK_FILES})"
    if bridge is not None and _cancel_requested(bridge, task.task_id):
        raise TaskCancelled()
    ctx = ""
    for f in task.files:
        chunk = tools.read_file(f)
        if len(ctx) + len(chunk) > MAX_TASK_CTX_CHARS:
            return (f"[error] context too large (> {MAX_TASK_CTX_CHARS} chars); "
                    "请减少文件数量或改喂更小的文件")
        ctx += f"\n--- file: {f} ---\n" + chunk + "\n"
    # 模型路由：显式 model > task_type 路由 > 全局默认
    routes = cfg.get("routes") or {}
    model = task.model or routes.get(task.task_type or "") or cfg["model"]
    system = (
        "你是运行在本地的离线AI助手，可读取用户工作区文件。"
        "根据任务直接给出结论或生成内容；若需产出文件，请在回复中说明将写入的路径。"
    )
    base_prompt = f"{system}\n\n{SYSTEM_PROMPT}\n任务: {task.prompt}\n"
    if ctx:
        clipped = ctx[:3000]
        base_prompt += "文件内容（节选）：\n" + clipped + "\n"
        if len(ctx) > len(clipped):
            base_prompt += ("（文件内容较长，仅显示前 3000 字符；"
                            "需要更多内容请用 read_file 继续读取。）\n")
    # 是否要求产出文件（决定是否点名 write_file、以及是否兜底落盘）
    write_required = any(k in (task.prompt or "") for k in
                         ("写入", "写文件", "写进", "写到", "保存", "生成文件",
                          "新建文件", "输出到"))
    wrote = False
    nudged = False
    # 需要产出文件时显式点名工具与目标路径：小模型否则会一直"读"而不"写"
    if write_required:
        target = _guess_target(task.prompt)
        base_prompt += ("本任务要求产出文件：内容读够之后立即用 write_file 写入"
                        + ("%s" % target if target else "指定路径")
                        + "，再用 finish 汇报。已经读过的文件不要重复读。\n")
        if target:
            base_prompt += "write_file 的 Input 第一行必须正好是：%s\n" % target
    base_prompt += "\n请开始：\n"
    max_steps = int(cfg.get("agent_max_steps", 4))
    last = ""
    last_obs = ""
    seen = set()          # (action, arg) 去重，防模型原地空转耗尽步数
    fails = {}            # (action, arg) → 连续失败次数
    steps = []            # 只保留最近 2 步，避免 prompt 随步数膨胀撑爆 6144 上下文
    for step in range(max_steps):
        if bridge is not None and _cancel_requested(bridge, task.task_id):
            raise TaskCancelled()          # 步间取消：不打断当前推理，但不再进入下一步
        prompt = base_prompt + "".join(
            "%s\nObservation: %s\n" % (prev, obs[:1500]) for prev, obs in steps)
        out = run_llm(cfg, prompt, temperature=0.2, model=model, num_predict=400)
        if out.startswith("[error]"):
            return out
        last = out.strip()
        if not last:
            return ("[error] 模型未返回内容（可能上下文过长或模型异常）；"
                    "请重试，或把任务拆小、换用更小的模型。")
        action, arg = _parse_action(last)
        answer = (arg.strip() or last) if action == "finish" else last
        if action is None or action == "finish":
            # 任务要求产出文件却没写：先提醒一次，仍不写就按目标路径确定性落盘。
            # （小模型经常直接给答案或 finish，用户拿到的结果其实没有落盘）
            if write_required and not wrote:
                if not nudged and step + 1 < max_steps:
                    nudged = True
                    target = _guess_target(task.prompt) or "任务要求的路径"
                    steps.append((last, "[system] 你还没有写文件。请先调用 write_file 把结果写入 %s，"
                                       "再用 finish 汇报。" % target))
                    if len(steps) > 2:
                        steps.pop(0)
                    continue
                target = _guess_target(task.prompt)
                if target:
                    res = tools.write_file(_norm_path(target, tools), answer,
                                           task_id=task.task_id)
                    if res.startswith("[ok]"):
                        return "已按任务要求把结果写入 %s。\n%s" % (target, res)
                    return "%s\n\n（写入 %s 失败：%s）" % (answer, target, res[:200])
            if bridge is not None:
                bridge.log(f"[agent] {task.task_id} step{step}: 收尾（finish）")
            return answer
        observation = _run_action(action, arg, tools, task_id=task.task_id)
        if action == "write_file" and observation.startswith("[ok]"):
            wrote = True
        if observation.startswith(("[error]", "[blocked]")):
            observation += "\n（提示：换一个路径或改用其他工具继续尝试，不要直接放弃。）"
        if bridge is not None:
            bridge.log(f"[agent] {task.task_id} step{step}: {action}({arg}) "
                       f"-> {len(observation)} chars")
        key = (action, arg)
        if key in seen and not observation.startswith(("[error]", "[blocked]")):
            # 原地打转是小模型最常见的"卡住"方式，直接把下一步指出来
            observation += ("\n（注意：这一步刚才已经做过，不要重复。"
                            "请直接进行下一步：用 write_file 写入结果，或 finish 汇报。）")
        if key in seen and observation.startswith(("[error]", "[blocked]")):
            # 同一个动作连续失败到第 3 次才放弃：只失败 1~2 次多半是模型在试探
            # 不存在的输出文件（读一个还没创建的文件很常见），立刻终止反而会误杀
            fails[key] = fails.get(key, 1) + 1
            if fails[key] >= 3:
                if bridge is not None:
                    bridge.log(f"[agent] {task.task_id}: 重复失败动作，提前终止")
                return f"[error] 工具调用重复失败：{observation}"
            observation += ("\n（同一动作已失败过，不要再试相同参数；"
                            "换路径、换工具，或直接 write_file / finish。）")
        seen.add(key)
        last_obs = observation
        steps.append((last, observation))
        if len(steps) > 2:
            steps.pop(0)
    if bridge is not None:
        bridge.log(f"[agent] {task.task_id}: 达到最大步数 {max_steps}，返回最后一次输出")
    action, _arg = _parse_action(last)
    if action is not None:
        # 最后一步仍是工具调用 = 没走完，返回可读错误而不是把 Action 原文丢给用户
        return ("[error] 未能在 %d 步内完成。最后一步工具结果：\n%s"
                % (max_steps, (last_obs or "（无）")[:300]))
    return last


def main() -> None:
    cfg = load_config()
    # config 的 ollama_base 优先（统一收敛到 127.0.0.1，避开 IPv6 回退延迟）
    os.environ["OLLAMA_HOST"] = str(
        cfg.get("ollama_base") or "http://127.0.0.1:11434"
    ).replace("localhost", "127.0.0.1")
    bridge = Bridge(cfg["workspace_root"], cfg["bridge_dir"])
    # 写操作默认允许（带快照 + 审计可回滚）；代码执行默认关闭，需显式开启
    agent_cfg = cfg.get("agent") or {}
    audit = Audit(cfg["workspace_root"])
    tools = Tools(cfg["workspace_root"],
                  allow_python=bool(agent_cfg.get("allow_python", False)),
                  audit=audit)
    # 定时任务：与 agent 同进程后台跑，结果写 <data>/schedule.log
    try:
        import scheduler
        from server.paths import data_dir
        scheduler.start_thread(
            str(data_dir()),
            lambda p, m: run_llm(cfg, p, temperature=0.3, model=m or None))
    except Exception as e:
        print(f"[offline-agent] 定时任务未启用: {e}", flush=True)
    recovered = bridge.reset_running()
    print(f"[offline-agent] started model={cfg['model']} bridge={bridge.root}", flush=True)
    if recovered:
        print(f"[offline-agent] recovered {recovered} interrupted task(s) "
              f"(running -> pending)", flush=True)
    while True:
        for path, task in bridge.list_pending():
            try:
                bridge.mark_running(path, task)
            except Exception as e:
                # 任务文件可能被外部删除/磁盘异常；跳过而不是让整个 agent 退出
                bridge.log(f"[skip] 无法标记运行 {task.task_id}: {e}")
                continue
            try:
                if _cancel_requested(bridge, task.task_id):
                    task.result = "[cancelled] 任务在开始前被取消"
                    task.status = "cancelled"
                else:
                    bridge.log(f"start {task.task_id}: {task.prompt[:50]}")
                    task.result = handle_task(cfg, bridge, task, tools)
                    task.status = "done"
            except TaskCancelled:
                task.result = "[cancelled] 任务已取消"
                task.status = "cancelled"
                bridge.log(f"cancelled {task.task_id}")
            except Exception as e:
                task.result = f"[error] {e}"
                task.status = "error"
            bridge.write_outbox(task)
            bridge.log(f"done {task.task_id} -> {task.status}")
            # 清理取消标记，避免影响同 id 的后续任务
            try:
                (bridge.inbox_path() / (task.task_id + ".cancel")).unlink()
            except Exception:
                pass
            try:
                path.unlink()
            except Exception:
                pass
        time.sleep(cfg.get("poll_interval", 3))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[offline-agent] stopped", flush=True)
