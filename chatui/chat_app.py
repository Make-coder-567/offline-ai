"""本地离线AI聊天界面 (Ollama), PySide6/Qt 实现。纯本地推理, 断网可用, 无云端依赖。

UI: 令牌化设计系统 + 自定义消息组件(头像/气泡/时间戳/悬停复制/思考动画/欢迎卡/状态胶囊)。
分发方式: 直接转发 offline_chat.exe (单文件, 已内置便携 Ollama)。
首次运行若本机无 Ollama 服务, 自动把内置 ollama 拷到 %LOCALAPPDATA%/offline_chat_ollama 并拉起 serve;
模型已随 exe 离线打包(<=4B 共 9 个), 首次运行自动用 ollama create 导入本地库, 无需联网。本机已装 Ollama 时直接用系统服务。
打包 exe: pyinstaller --onefile --windowed --name offline_chat --add-data "ollama;ollama" chat_app.py
"""
import sys
import os
import time
import json
import html
import re
import base64
import concurrent.futures
import shutil
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QTextEdit, QPushButton, QLabel, QFrame, QScrollArea, QMenu,
                               QSizePolicy, QGraphicsOpacityEffect, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QSpinBox, QPlainTextEdit, QMessageBox,
                               QFileDialog, QLineEdit, QListWidget, QListWidgetItem,
                               QToolTip)
from PySide6.QtCore import (Qt, QThread, Signal, QTimer, QSize, QEvent, QRect, QRectF, QObject,
                            QPropertyAnimation, QEasingCurve, QSettings, QSharedMemory)
from PySide6.QtGui import (QPixmap, QPainter, QPainterPath, QPen, QColor, QIcon, QAction,
                           QGuiApplication, QBrush, QLinearGradient, QShortcut, QKeySequence,
                           QTextCursor, QCursor)

OLLAMA_BASE = "http://127.0.0.1:11434"  # 用 IPv4 直连, 避免 localhost->::1 回退延迟

# 深度思考（draft → critique → revise）两轮追加指令
DEEP_CRITIQUE = ("请批判性检查上面这份回答：指出事实错误、遗漏的关键点、逻辑或表述问题，"
                 "用 3-5 条简短要点列出。不要重写全文，只列问题。")
DEEP_REVISE = ("请根据上面的自检意见重写最终答案：修正问题、补齐遗漏、保持简洁。"
               "只输出最终答案正文，不要出现「根据意见」「修改说明」这类过程性文字。")

# Ollama 是本地服务, 强制不走 HTTP 代理(避免企业代理/沙箱代理劫截 localhost 请求)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 让 `from server import docparse / rag` 可用：打包态取 _MEIPASS，开发态取项目根
def _bundle_root():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, _bundle_root())
# sessions.py / voice.py 与 chat_app 同级：开发态加脚本目录，打包态加 _MEIPASS
for _p in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(__file__))):
    if _p:
        sys.path.insert(0, _p)

DEFAULT_SID = "default"      # 与 sessions.DEFAULT_SID 保持一致


# ==================== 本地数据目录 / 会话持久化 ====================
def _data_dir():
    """会话与设置落盘目录：打包态写 %LOCALAPPDATA%，开发态写脚本同目录/data。"""
    if getattr(sys, "frozen", False):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "offline_chat_data")
    else:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _store() -> "SessionStore":
    global _STORE
    if _STORE is None or _STORE.dir.parent != Path(_data_dir()):
        from sessions import SessionStore
        _STORE = SessionStore(_data_dir())
    return _STORE


_STORE = None
_CURRENT = {"sid": None}     # 当前会话 id（供 _session_path 定位，测试会直接删该文件）


def _session_path():
    """当前会话文件：多会话后落在 <data_dir>/sessions/<sid>.json。"""
    sid = _CURRENT["sid"] or DEFAULT_SID
    return str(_store().path_for(sid))


def _atomic_write_text(path, text):
    """临时文件 + rename，避免崩溃留下半个 JSON。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ==================== Markdown 轻量渲染（先转义再替换, 防注入） ====================
_MD_FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)
_MD_INLINE = re.compile(r"`([^`\n]+)`")
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_HEAD = re.compile(r"^(#{1,4})\s+(.+)$", re.M)
_MD_BULLET = re.compile(r"^[-*]\s+(.+)$", re.M)


def render_markdown(text):
    """把常用 Markdown 子集渲染为 Qt 富文本；代码块先摘出，内部不再做行内转换。"""
    blocks = []

    def stash(m):
        code = html.escape(m.group(1).rstrip("\n"))
        blocks.append('<pre style="font-family:Consolas,monospace;'
                      'background:#F1F3F8;">%s</pre>' % code)
        return "\x00%d\x00" % (len(blocks) - 1)

    t = _MD_FENCE.sub(stash, text or "")
    t = html.escape(t)
    t = _MD_INLINE.sub(
        lambda m: '<code style="font-family:Consolas,monospace;">%s</code>' % m.group(1), t)
    t = _MD_BOLD.sub(r"<b>\1</b>", t)
    t = _MD_HEAD.sub(lambda m: "<b>%s</b>" % m.group(2), t)
    t = _MD_BULLET.sub(r"• \1", t)
    t = t.replace("\n", "<br>")
    for i, b in enumerate(blocks):
        t = t.replace("\x00%d\x00" % i, b)
    return t


_CITE_RE = re.compile(r"\[(\d{1,2})\]")


def format_body(role, text):
    """AI 消息走 Markdown；用户/系统消息保持纯文本转义（避免误伤原文符号）。"""
    if role != "ai":
        return _rich(text)
    html = render_markdown(text)
    # 把 [1][2] 变成可点链接：点开能看到来源片段（见 ChatWindow._show_cite）
    link = _icon_color("moss", "#1F5C4A")
    return _CITE_RE.sub(lambda m: '<a href="cite:%s" style="color:%s;'
                                  'text-decoration:none;">[%s]</a>'
                                  % (m.group(1), link, m.group(1)), html)


def _strip_markdown(text):
    """把 Markdown 还原成近似纯文本，供"复制纯文本"使用。"""
    t = re.sub(r"```[^\n`]*\n(.*?)```", lambda m: m.group(1), text or "", flags=re.S)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", t)
    t = re.sub(r"^#{1,4}\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    return t


# ==================== 调试日志(排查接收方安装失败) ====================
def _debug_log_path():
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                            "offline_chat_debug.log")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "offline_chat_debug.log")


_DEBUG_FH = None


def log(msg):
    """同时写离线_chat_debug.log 与 stdout, 便于接收方把日志发回排查。"""
    global _DEBUG_FH
    ts = time.strftime("%H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    try:
        if _DEBUG_FH is None or _DEBUG_FH.closed:
            _DEBUG_FH = open(_debug_log_path(), "a", encoding="utf-8")
        _DEBUG_FH.write(line + "\n")
        _DEBUG_FH.flush()
    except Exception:
        pass
    print(line)


# 记录内置模型导入失败清单, 供 _bootstrap 在 UI 上明确提示
_bundled_install_failures = []


# ==================== 内置 Ollama 便携版引导 ====================
def _persistent_ollama_dir():
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~/.offline_chat")
    return os.path.join(base, "offline_chat_ollama")


def _resource_dir():
    """打包态下返回内置资源(ollama/bundle_models/app.ico)所在目录。
    兼容 onefile(sys._MEIPASS) 与 onedir(exe 同级或 _internal 子目录) 多种布局:
    直接定位"包含 ollama 或 bundle_models 的目录", 不依赖 _MEIPASS 具体指向。"""
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(sys._MEIPASS)
    base = os.path.dirname(os.path.abspath(sys.executable))
    candidates.append(base)
    candidates.append(os.path.join(base, "_internal"))
    for c in candidates:
        if os.path.isdir(os.path.join(c, "ollama")) or \
           os.path.isdir(os.path.join(c, "bundle_models")):
            return c
    return candidates[-1] if candidates else base


def _ollama_exe():
    if getattr(sys, "frozen", False):
        persist = os.path.join(_persistent_ollama_dir(), "ollama.exe")
        if os.path.exists(persist):
            return persist
        p = os.path.join(_resource_dir(), "ollama", "ollama.exe")
        if os.path.exists(p):
            return p
        # 轻量版（单 exe，未内置 ollama）: 回退到系统安装的 ollama
        return shutil.which("ollama") or "ollama"
    return "ollama"


def _copy_ollama_to_persistent():
    if not getattr(sys, "frozen", False):
        return False
    src = getattr(sys, "_MEIPASS", None)
    if not src:
        return False
    src = os.path.join(src, "ollama")
    if not os.path.isdir(src):
        return False
    dst = _persistent_ollama_dir()
    # 用源文件大小+mtime 做版本戳：程序升级后不会继续复用旧引擎
    try:
        stamp = "%d-%d" % (os.path.getsize(os.path.join(src, "ollama.exe")),
                           int(os.path.getmtime(os.path.join(src, "ollama.exe"))))
    except OSError:
        stamp = ""
    marker = os.path.join(dst, ".copied")
    if stamp and os.path.exists(marker):
        try:
            with open(marker, encoding="utf-8") as f:
                if f.read().strip() == stamp:
                    return True
        except Exception:
            pass
    try:
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        if stamp:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(stamp)
        return True
    except Exception:
        return False


def _is_serving():
    try:
        _OPENER.open(OLLAMA_BASE + "/api/version", timeout=2)
        return True
    except Exception:
        return False


def ensure_ollama():
    log("ensure_ollama: frozen=%s" % getattr(sys, "frozen", False))
    if _is_serving():
        log("ensure_ollama: 11434 已可用(可能本机已装系统 Ollama, 直接复用)")
        return True
    if getattr(sys, "frozen", False):
        _copy_ollama_to_persistent()
    exe = _ollama_exe()
    log("ensure_ollama: 选用 ollama=%s" % exe)
    # 纯命令名（轻量版回退系统 PATH）不校验绝对路径
    if os.path.dirname(exe) and not os.path.exists(exe):
        log("ensure_ollama: ollama.exe 不存在! 请确认 _internal/ollama/ollama.exe 随包分发且未被杀软删除")
        return False
    olama_dir = os.path.dirname(exe)
    if olama_dir and olama_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = olama_dir + os.pathsep + os.environ.get("PATH", "")
    # 把 serve 输出写进独立日志, 便于排查(仍 DETACHED 让其独立于本进程存活)
    serve_log = os.path.join(os.path.dirname(_debug_log_path()), "offline_chat_ollama_serve.log")
    try:
        lf = open(serve_log, "a", encoding="utf-8")
        p = subprocess.Popen([exe, "serve"], creationflags=0x00000008,  # DETACHED_PROCESS
                              stdout=lf, stderr=lf)
        log("ensure_ollama: 已启动 serve pid=%s (日志=%s)" % (p.pid, serve_log))
    except Exception as e:
        log("ensure_ollama: 启动 serve 失败: %s" % e)
        return False
    # 拉长等待(慢机/杀软首次扫描 27MB 二进制可能耗时较久)
    for i in range(120):
        if _is_serving():
            log("ensure_ollama: 服务就绪 (%ds)" % i)
            return True
        if p.poll() is not None:
            log("ensure_ollama: serve 进程已退出 rc=%s —— 多半被杀软拦截或端口被占用, 见 %s"
                % (p.returncode, serve_log))
            return False
        time.sleep(1)
    log("ensure_ollama: 120s 超时仍未就绪, 见 %s" % serve_log)
    return False


def has_model(model):
    try:
        with _OPENER.open(OLLAMA_BASE + "/api/tags", timeout=5) as r:
            d = json.loads(r.read().decode())
        return any(m.get("name") == model for m in d.get("models", []))
    except Exception:
        return False


def pull_model(model):
    exe = _ollama_exe()
    subprocess.run([exe, "pull", model], timeout=None)


def _installed_names():
    """一次性取回已装模型名集合（比逐模型 has_model 少 8 次 HTTP 往返）。"""
    try:
        with _OPENER.open(OLLAMA_BASE + "/api/tags", timeout=5) as r:
            d = json.loads(r.read().decode())
        return {m.get("name") for m in d.get("models", [])}
    except Exception:
        return set()


def _install_bundled_models(progress_cb=None):
    """首次运行时把内置 GGUF 通过 `ollama create` 导入本地模型库(完全离线)。

    - 已存在的模型跳过；缺失 Modelfile/GGUF 计为失败，不静默吞错。
    - 两路并行导入（磁盘 IO 是瓶颈，2 路足够），进度经 progress_cb 上报。
    - 仅打包态(frozen)生效。
    """
    global _bundled_install_failures
    _bundled_install_failures = []
    if not getattr(sys, "frozen", False):
        return {"ok": 0, "total": 0, "failures": []}
    base = _resource_dir()
    log("_install_bundled_models: resource_dir=%s" % base)
    if not base:
        log("_install_bundled_models: 资源目录定位失败")
        return {"ok": 0, "total": 0, "failures": []}
    manifest_path = os.path.join(base, "bundle_manifest.json")
    if not os.path.isfile(manifest_path):
        log("_install_bundled_models: 找不到 %s" % manifest_path)
        return {"ok": 0, "total": 0, "failures": []}
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        log("_install_bundled_models: 读取清单失败: %s" % e)
        return {"ok": 0, "total": 0, "failures": []}
    exe = _ollama_exe()
    if not os.path.exists(exe):
        log("_install_bundled_models: ollama.exe 不存在")
        return {"ok": 0, "total": 0, "failures": []}

    total = len(manifest)
    installed = _installed_names()
    failures = []
    lock = threading.Lock()
    done = 0

    def report(model, status):
        nonlocal done
        with lock:
            done += 1
            cur = done
        if progress_cb:
            try:
                progress_cb(cur, total, model, status)
            except Exception:
                pass

    jobs = []
    for m in manifest:
        model = m.get("model")
        modelfile = os.path.join(base, "bundle_models", m.get("safe", ""), "Modelfile")
        if not model or not os.path.isfile(modelfile):
            # Modelfile 缺失 = 打包缺陷(历史版本曾漏打), 必须计为失败,
            # 否则 0/9 全跳过也会被当成"无失败", 误导用户且不重试。
            log("  [FAIL] 缺 Modelfile: %s (应为 bundle_models/%s/Modelfile)"
                % (model or "?", m.get("safe", "?")))
            if model:
                failures.append(model)
            report(model or "?", "fail")
            continue
        if model in installed:      # 已导入则跳过
            log("  已存在, 跳过: %s" % model)
            report(model, "skip")
            continue
        gguf = os.path.join(os.path.dirname(modelfile), m.get("gguf_name", ""))
        if not os.path.isfile(gguf) or os.path.getsize(gguf) < 1024 * 1024:
            log("  [FAIL] GGUF 缺失或损坏(<1MB): %s" % gguf)
            failures.append(model)
            report(model, "fail")
            continue
        if m.get("mmproj_name"):
            mp = os.path.join(os.path.dirname(modelfile), m["mmproj_name"])
            if not os.path.isfile(mp) or os.path.getsize(mp) < 1024 * 1024:
                log("  [FAIL] mmproj 缺失或损坏(<1MB): %s" % mp)
                failures.append(model)
                report(model, "fail")
                continue
        jobs.append((model, modelfile, gguf))

    def _import_one(job):
        model, modelfile, gguf = job
        err_msg = ""
        # 两路并行时 Ollama 偶发模型库锁冲突；失败自动串行重试一次
        for attempt in range(2):
            log("  开始导入: %s (from %s)%s"
                % (model, gguf, " [重试]" if attempt else ""))
            t0 = time.time()
            try:
                r = subprocess.run([exe, "create", model, "-f", modelfile],
                                   cwd=os.path.dirname(modelfile),
                                   timeout=1800, check=False,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out = (r.stdout or b"").decode(errors="replace")
                err = (r.stderr or b"").decode(errors="replace")
                if r.returncode == 0 and model in _installed_names():
                    log("  [OK] %s (%.1fs)" % (model, time.time() - t0))
                    return model, True, ""
                log("  [FAIL] %s rc=%s\n    stdout=%s\n    stderr=%s"
                    % (model, r.returncode, out[-400:], err[-400:]))
                err_msg = err[-200:]
            except Exception as e:
                log("  [FAIL] %s 异常: %s" % (model, e))
                err_msg = str(e)
            if attempt == 0:
                log("  1.5s 后重试一次: %s" % model)
                time.sleep(1.5)
        return model, False, err_msg

    if jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_import_one, j) for j in jobs]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    model, ok, _msg = fut.result()
                except Exception as e:
                    log("  [FAIL] 导入任务异常: %s" % e)
                    continue
                if not ok:
                    with lock:
                        failures.append(model)
                report(model, "ok" if ok else "fail")

    ok_count = total - len(failures)
    log("_install_bundled_models: 成功 %d / 共 %d, 失败=%s"
        % (ok_count, total, failures))
    # 真正全部成功才写标记; 跳过/缺失也计入失败口径, 避免误标"已完成"
    if ok_count == total and not failures:
        try:
            os.makedirs(_persistent_ollama_dir(), exist_ok=True)
            open(os.path.join(_persistent_ollama_dir(), ".bundled_models_v1"), "w").close()
        except Exception:
            pass
    _bundled_install_failures = failures
    return {"ok": ok_count, "total": total, "failures": failures}


# ==================== 推理线程 ====================
class DocWorker(QThread):
    """后台解析文档：扫描 PDF / 图片 OCR 可能耗时数秒，不能在 UI 线程里跑。"""
    done = Signal(str, str)       # path, text
    failed = Signal(str, str)     # path, error

    def __init__(self, path, ocr=True, parent=None):
        super().__init__(parent)
        self.path = path
        self.ocr = ocr

    def run(self):
        dp = docparse_module()
        if dp is None:
            _emit(self.failed, self.path, "文档解析组件不可用（打包时未包含 server.docparse）")
            return
        try:
            text = dp.extract_text(self.path, ocr_fallback=self.ocr)
        except Exception as e:
            _emit(self.failed, self.path, str(e))
            return
        if text.startswith("[error]"):
            _emit(self.failed, self.path, text)
        else:
            _emit(self.done, self.path, text)


class Worker(QThread):
    token = Signal(str)
    finished = Signal()
    errored = Signal(str)
    stats = Signal(dict)          # eval_count / eval_duration / prompt_eval_count

    def __init__(self, model, messages, temperature=0.3, num_ctx=6144):
        super().__init__()
        self.model = model
        self.messages = messages           # 完整多轮历史 + 当前用户消息
        self.temperature = temperature
        self.num_ctx = num_ctx
        self._cancelled = False
        self._resp = None
        self.result_text = ""

    def cancel(self):
        """中断生成：关闭流式连接，读取循环会在下一次迭代退出。"""
        self._cancelled = True
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass

    def run(self):
        try:
            payload = json.dumps(
                {"model": self.model,
                 "messages": self.messages,
                 "stream": True,
                 # num_ctx 可通过"设置"调整；qwen3:4b 在 4096 下会输出乱码(实测)
                 "options": {"temperature": self.temperature,
                             "num_ctx": self.num_ctx,
                             "keep_alive": "10m"}}
            ).encode()
            req = urllib.request.Request(
                OLLAMA_BASE + "/api/chat", data=payload,
                headers={"Content-Type": "application/json"})
            # CPU 上大模型冷加载+长 prompt prefill 可能 >120s, 放宽到 600s 防误判超时
            with _OPENER.open(req, timeout=600) as resp:
                self._resp = resp
                buf = ""
                for line in resp:
                    if self._cancelled:
                        break
                    line = line.decode().strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    msg = obj.get("message", {}) or {}
                    # thinking 模型(如误用未加 /no_think 的 qwen3) content 可能为空,
                    # 回退展示 thinking, 避免界面上出现"空气泡"
                    tok = msg.get("content") or msg.get("thinking") or ""
                    if tok:
                        buf += tok
                        self.result_text = buf
                        _emit(self.token, buf)
                    if obj.get("done"):
                        _emit(self.stats, {k: obj.get(k, 0) for k in (
                            "eval_count", "eval_duration",
                            "prompt_eval_count", "total_duration")})
                        break
        except urllib.error.URLError as e:
            if not self._cancelled:
                _emit(self.errored, "连接 Ollama 失败：%s  请确认 Ollama 已启动" % e)
        except Exception as e:
            if not self._cancelled:      # 用户主动停止引起的异常不算错误
                _emit(self.errored, "错误：%s" % e)
        finally:
            _emit(self.finished)


# ==================== 跨线程信号桥 (worker 线程 -> 主线程) ====================
class Bridge(QObject):
    status = Signal(str, str)   # state, text
    sys = Signal(str)           # 系统提示
    models = Signal(list)       # 模型名列表
    progress = Signal(str)      # 模型导入进度文本
    error = Signal(str)         # 错误提示
    voice_text = Signal(str)    # 语音转写结果（回填输入框）


def _emit(sig, *args):
    """线程里安全发信号：窗口已销毁时 Qt 会抛 RuntimeError，不能让它打断后台线程。

    典型场景：启动引导/录音/解析线程还在跑，用户已经关掉窗口。
    """
    try:
        sig.emit(*args)
    except RuntimeError:
        pass


# ==================== 设计令牌 / 样式 ====================
def _rich(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


def build_qss():
    return """
    *{font-family:'Microsoft YaHei UI','Microsoft YaHei','Segoe UI',sans-serif;
       font-size:13px;color:#1B2033;}
    QMainWindow{background:#F6F7FB;}
    /* 顶栏 */
    #hdr{background:#FFFFFF;border-bottom:1px solid #E6E8F0;}
    #appName{color:#1B2033;font-size:15px;font-weight:bold;}
    #appSub{color:#5A6172;font-size:12px;}
    #modelBtn{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:8px;color:#1B2033;
              font-size:12.5px;padding:5px 26px 5px 12px;min-width:118px;text-align:left;}
    #modelBtn:hover{background:#F1F3F8;border-color:#D3D7E3;}
    #modelBtn::menu-indicator{subcontrol-position:right center;right:9px;width:12px;height:12px;}
    QMenu{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:12px;padding:6px;}
    QMenu::item{padding:7px 12px;border-radius:8px;color:#1B2033;}
    QMenu::item:selected{background:#F1F3F8;}
    QMenu::item:checked{color:#364FC7;font-weight:bold;}
    /* 对话区 */
    QScrollArea{background:#F6F7FB;border:none;}
    #feed{background:#F6F7FB;}
    QScrollBar:vertical{background:transparent;width:10px;margin:2px;}
    QScrollBar:vertical:handle{background:#D3D7E3;border-radius:5px;min-height:44px;}
    QScrollBar:vertical:handle:hover{background:#B9BFCF;}
    QScrollBar:vertical:add-line,QScrollBar:vertical:sub-line{height:0;}
    QScrollBar:vertical:add-page,QScrollBar:vertical:sub-page{background:transparent;}
    #bubbleAI{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:16px;}
    #bubbleME{background:#EDF0FE;border:1px solid #DCE3FB;border-radius:16px;}
    #bubbleTxt{background:transparent;color:#1B2033;font-size:13.5px;selection-background-color:#DCE3FB;}
    #metaLbl{color:#8A91A3;font-size:11px;background:transparent;}
    #thinkTxt{color:#5A6172;font-size:13px;background:transparent;}
    #copyBtn{background:rgba(255,255,255,0.94);border:1px solid #E6E8F0;border-radius:11px;
             color:#5A6172;font-size:11px;padding:2px 7px;}
    #copyBtn:hover{color:#364FC7;border-color:#DCE3FB;background:#EDF0FE;}
    #sysChip{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:14px;}
    #sysChip QLabel{color:#5A6172;font-size:12px;background:transparent;}
    #errChip{background:#FDE8E8;border:1px solid #F6C9C9;border-radius:14px;}
    #errChip QLabel{color:#B3261E;font-size:12px;background:transparent;}
    /* 欢迎卡 */
    #welcome{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:16px;}
    #wTitle{color:#1B2033;font-size:21px;font-weight:bold;}
    #wSub{color:#5A6172;font-size:13px;}
    #wStatus{color:#15803D;font-size:12.5px;}
    #chip{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:14px;color:#5A6172;
          font-size:12.5px;padding:6px 13px;}
    #chip:hover{color:#364FC7;border-color:#DCE3FB;background:#EDF0FE;}
    /* 输入区 */
    #composerCard{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:16px;}
    #input{background:transparent;border:none;color:#1B2033;font-size:14px;selection-background-color:#DCE3FB;}
    #hint{color:#8A91A3;font-size:11px;}
    #send{background:#3B5BDB;border:none;border-radius:12px;}
    #send:hover{background:#364FC7;}
    #send:pressed{background:#2F47B4;}
    #send:disabled{background:#CBD0DE;}
    """


# ==================== 矢量图标 (QPainter 绘制, 无外部资源) ====================
def _dpr():
    """图标按屏幕 DPR 渲染；至少 2x，保证 1x 屏下也清晰。"""
    try:
        return max(2.0, float(QGuiApplication.primaryScreen().devicePixelRatio()))
    except Exception:
        return 2.0


def _p_bolt():
    p = QPainterPath()
    p.moveTo(13, 2); p.lineTo(4.6, 13.4); p.lineTo(10.8, 13.4)
    p.lineTo(9.6, 22); p.lineTo(19.4, 9.6); p.lineTo(13, 9.6); p.closeSubpath()
    return p


def _p_send():
    p = QPainterPath()
    p.moveTo(3.2, 20.6); p.lineTo(21, 12); p.lineTo(3.2, 3.4)
    p.lineTo(3.2, 10); p.lineTo(12.6, 12); p.lineTo(3.2, 14); p.closeSubpath()
    return p


def _p_copy():
    p = QPainterPath()
    p.addRoundedRect(QRectF(9, 9, 11, 11), 2.5, 2.5)
    p.addRoundedRect(QRectF(4.5, 3.5, 11, 11), 2.5, 2.5)
    return p


def _p_check():
    p = QPainterPath()
    p.moveTo(5, 13); p.lineTo(9.6, 17.6); p.lineTo(19, 7)
    return p


def _p_mic():
    """麦克风： capsule 主体 + 支架。"""
    p = QPainterPath()
    p.addRoundedRect(QRectF(9.0, 2.5, 6.0, 11.0), 3.0, 3.0)
    return p


def _p_mic_stand():
    p = QPainterPath()
    p.moveTo(6.5, 11.0); p.quadTo(12.0, 15.5, 17.5, 11.0)
    p.moveTo(12.0, 15.0); p.lineTo(12.0, 21.0)
    return p


def _p_doc():
    """文档图标：折角纸张 + 三行文字。"""
    p = QPainterPath()
    p.moveTo(5.5, 2.5); p.lineTo(13.5, 2.5); p.lineTo(18.5, 7.5)
    p.lineTo(18.5, 21.5); p.lineTo(5.5, 21.5); p.closeSubpath()
    p.moveTo(13.5, 2.5); p.lineTo(13.5, 7.5); p.lineTo(18.5, 7.5)
    return p


def _p_doc_lines():
    p = QPainterPath()
    for y in (11.0, 14.5, 18.0):
        p.moveTo(8.5, y); p.lineTo(15.5, y)
    return p


def make_icon(kind, color, size):
    S = 24.0
    dpr = _dpr()
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / S, size / S)
    col = QColor(color)
    if kind == "stop":
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2.5, 2.5)
    elif kind in ("bolt", "send"):
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPath(_p_bolt() if kind == "bolt" else _p_send())
    elif kind in ("copy", "check"):
        pen = QPen(col)
        pen.setWidthF(2.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(_p_copy() if kind == "copy" else _p_check())
    elif kind == "mic":
        pen = QPen(col)
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(col)
        p.drawPath(_p_mic())
        p.setBrush(Qt.NoBrush)
        p.drawPath(_p_mic_stand())
    elif kind == "doc":
        pen = QPen(col)
        pen.setWidthF(1.8)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(_p_doc())
        pen.setWidthF(1.4)
        p.setPen(pen)
        p.drawPath(_p_doc_lines())
    elif kind == "more":
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for cx in (7.0, 12.0, 17.0):
            p.drawEllipse(QRectF(cx - 1.1, 10.9, 2.2, 2.2))
    elif kind == "download":
        pen = QPen(col)
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(12, 4, 12, 15)          # 竖线
        p.drawLine(8, 11, 12, 15)          # 箭头左
        p.drawLine(16, 11, 12, 15)         # 箭头右
        p.drawLine(5, 19.5, 19, 19.5)      # 托盘
    p.end()
    return QIcon(pm)


_VOICE_OK = {}
# 当前主题令牌（BubbleItem 等动态创建的控件靠它取图标颜色）
_THEME_TOKENS = {}


def _icon_color(key="muted", default="#5F6B64"):
    return _THEME_TOKENS.get(key, default)


def _free_mem_gb() -> float:
    """可用物理内存（GB）。取不到就返回 -1，调用方据此跳过检查。"""
    try:
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _MS()
        st.dwLength = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullAvailPhys / (1024.0 ** 3)
    except Exception:
        return -1.0


def degenerate_output(text: str) -> bool:
    """判断模型输出是否为"退化垃圾"（00000、,,,,, 这类）。

    小模型在量化文件不完整 / 参数不兼容 / KV 损坏时会吐出这种重复字符流；
    直接当答案展示既误导用户，又会污染下一轮上下文。
    只做两条保守判断，避免误伤正常英文回答：
    """
    t = (text or "").strip()
    if len(t) < 12:
        return False
    from collections import Counter
    c = Counter(t)
    top = c.most_common(1)[0][1]
    if len(t) >= 30 and top / len(t) >= 0.6:   # 长文本里单字符占 60%+
        return True
    return len(c) <= 3                          # 全文只有 2~3 种字符


def voice_stt_ok() -> bool:
    if "stt" not in _VOICE_OK:
        try:
            import voice
            _VOICE_OK["stt"] = bool(voice.stt_available())
        except Exception:
            _VOICE_OK["stt"] = False
    return _VOICE_OK["stt"]


def voice_tts_ok() -> bool:
    if "tts" not in _VOICE_OK:
        try:
            import voice
            _VOICE_OK["tts"] = bool(voice.tts_available())
        except Exception:
            _VOICE_OK["tts"] = False
    return _VOICE_OK["tts"]


def docparse_module():
    """延迟导入 server.docparse（打包态从 _MEIPASS 取），失败返回 None。"""
    try:
        from server import docparse
        return docparse
    except Exception:
        try:
            import docparse  # type: ignore
            return docparse
        except Exception:
            return None


def _badge_pixmap(size, gradient, glyph_draw):
    dpr = _dpr()
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if gradient:
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor("#4C6EF5"))
        g.setColorAt(1, QColor("#3B5BDB"))
        p.setBrush(QBrush(g))
    else:
        p.setBrush(QColor("#E7E9F1"))
    p.setPen(Qt.NoPen)
    r = size * 0.3
    p.drawRoundedRect(QRectF(1.5, 1.5, size - 3, size - 3), r, r)
    p.scale(size / 24.0, size / 24.0)
    glyph_draw(p)
    p.end()
    return pm


def make_ai_avatar(size=30):
    return _badge_pixmap(size, True, lambda p: (p.setBrush(QColor("#FFFFFF")), p.drawPath(_p_bolt())))


def make_user_avatar(size=30):
    def draw(p):
        p.setBrush(QColor("#5A6172"))
        p.drawEllipse(QRectF(8, 4, 8, 8))
        p.drawRoundedRect(QRectF(3.6, 15, 16.8, 7), 3.5, 3.5)
    return _badge_pixmap(size, False, draw)


def make_logo(size=30):
    return make_ai_avatar(size)


# ==================== 状态胶囊 ====================
class StatusPill(QWidget):
    STATES = {
        "online":   dict(bg="#E7F8EE", bd="#CBEBD6", tx="#15803D", dot="#22C55E"),
        "pulling":  dict(bg="#FEF3D8", bd="#F6E2B0", tx="#B45309", dot="#F59E0B"),
        "checking": dict(bg="#F1F3F8", bd="#E6E8F0", tx="#5A6172", dot="#9CA3AF"),
        "offline":  dict(bg="#F1F3F8", bd="#E6E8F0", tx="#5A6172", dot="#9CA3AF"),
        "error":    dict(bg="#FDE8E8", bd="#F6C9C9", tx="#B3261E", dot="#EF4444"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusPill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 0, 12, 0)
        lay.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.txt = QLabel("检查服务中…")
        lay.addWidget(self.dot)
        lay.addWidget(self.txt)
        self._setup_pulse()
        self.set_state("checking")

    def _setup_pulse(self):
        try:
            self._eff = QGraphicsOpacityEffect(self.dot)
            self.dot.setGraphicsEffect(self._eff)
            self._anim = QPropertyAnimation(self._eff, b"opacity", self)
            self._anim.setStartValue(0.35)
            self._anim.setEndValue(1.0)
            self._anim.setDuration(1100)
            self._anim.setEasingCurve(QEasingCurve.InOutSine)
            self._anim.setLoopCount(-1)
            self._anim.start()
        except Exception:
            self._eff = None

    def set_state(self, state, text=None):
        self._state = state
        s = self.STATES.get(state, self.STATES["checking"])
        if text is not None:
            self.txt.setText(text)
        tx, dot = s["tx"], s["dot"]
        t = getattr(self, "_tokens", None)
        if t:      # 跟随主题：夜读模式下浅底绿字会糊掉，直接用主色
            key = {"online": "moss", "pulling": "amber", "error": "seal"}.get(state)
            tx = t.get(key, t["muted"]) if key else t["muted"]
            dot = tx
        self.setStyleSheet(
            "#statusPill{background:transparent;border:none;}"
            "#statusPill QLabel{color:%s;font-size:12px;background:transparent;border:none;}"
            % tx)
        self.dot.setStyleSheet("background:%s;border-radius:4px;" % dot)

    def retheme(self, tokens):
        """主题切换时刷新配色（保留当前状态）。"""
        self._tokens = tokens
        self.set_state(getattr(self, "_state", "checking"))


# ==================== 消息气泡 ====================
class BubbleItem(QWidget):
    def __init__(self, role, text="", who="", ts="", action=None, parent=None,
                 on_cite=None):
        super().__init__(parent)
        self.role = role
        self._raw = text
        self.in_history = True      # 失败轮的用戶消息仍显示，但不写入上下文
        if role in ("sys", "error"):
            self.chip = QFrame()
            self.chip.setObjectName("errChip" if role == "error" else "sysChip")
            cl = QHBoxLayout(self.chip)
            cl.setContentsMargins(13, 5, 13, 5)
            cl.setSpacing(8)
            self.label = QLabel(_rich(text))
            self.label.setWordWrap(True)
            self.label.setTextFormat(Qt.RichText)
            cl.addWidget(self.label)
            if action:
                label, cb = action
                btn = QPushButton(label)
                btn.setObjectName("copyBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFocusPolicy(Qt.NoFocus)
                btn.clicked.connect(lambda _=False: cb())
                cl.addWidget(btn)
            outer = QHBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addStretch(1)
            outer.addWidget(self.chip)
            outer.addStretch(1)
            # 竖直固定：否则 feed 会把多余高度分给提示条，消息之间出现大空洞
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            return

        is_me = role == "me"
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        meta = QHBoxLayout()
        meta.setContentsMargins(16 if not is_me else 2, 0, 2, 0)
        meta.setSpacing(6)
        self.whoLbl = QLabel(who)
        self.timeLbl = QLabel(ts)
        self.whoLbl.setObjectName("metaLbl")
        self.timeLbl.setObjectName("metaLbl")
        if is_me:
            meta.addStretch(1)
            meta.addWidget(self.timeLbl)
            meta.addWidget(self.whoLbl)
        else:
            meta.addWidget(self.whoLbl)
            meta.addWidget(self.timeLbl)
            meta.addStretch(1)
        col.addLayout(meta)

        self.bubble = QFrame()
        self.bubble.setObjectName("bubbleME" if is_me else "bubbleAI")
        self.label = QLabel(format_body(role, text))
        self.label.setObjectName("bubbleTxt")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.RichText)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl = QVBoxLayout(self.bubble)
        # AI 用左侧 2px 墨绿线 + 16px 内距（无气泡底）；用户气泡保留 13px 内距
        if is_me:
            bl.setContentsMargins(13, 10, 13, 11)
        else:
            bl.setContentsMargins(16, 2, 4, 2)
        if on_cite is not None:
            self.label.setOpenExternalLinks(False)
            self.label.linkActivated.connect(
                lambda href: on_cite(href.split(":", 1)[-1]) if href.startswith("cite:") else None)
        bl.addWidget(self.label)
        col.addWidget(self.bubble)

        if is_me:
            outer.addStretch(1)
            outer.addLayout(col)
        else:
            outer.addLayout(col)
            outer.addStretch(1)

        # 操作条：常驻占位（26px），按钮在悬停时才显示，避免鼠标移入时整段跳动
        self.acts = QWidget()
        self.acts.setObjectName("acts")
        self.acts.setFixedHeight(26)
        al = QHBoxLayout(self.acts)
        al.setContentsMargins(0 if is_me else 16, 2, 0, 0)
        al.setSpacing(2)
        if is_me:
            al.addStretch(1)

        self.copyBtn = QPushButton("复制")
        self.copyBtn.setObjectName("actBtn")
        self.copyBtn.setIcon(make_icon("copy", _icon_color("muted", "#5A6172"), 13))
        self.copyBtn.setIconSize(QSize(13, 13))
        self.copyBtn.setCursor(Qt.PointingHandCursor)
        self.copyBtn.setFocusPolicy(Qt.NoFocus)
        self.copyBtn.clicked.connect(self._copy)
        self.copyBtn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.copyBtn.customContextMenuRequested.connect(self._copy_menu)
        al.addWidget(self.copyBtn)

        # 朗读（离线 TTS）：仅 AI 气泡有，且组件可用时才出现
        self.speakBtn = None
        if role == "ai" and voice_tts_ok():
            self.speakBtn = QPushButton("朗读")
            self.speakBtn.setObjectName("actBtn")
            self.speakBtn.setCursor(Qt.PointingHandCursor)
            self.speakBtn.setFocusPolicy(Qt.NoFocus)
            self.speakBtn.clicked.connect(self._speak)
            al.addWidget(self.speakBtn)

        if not is_me:
            al.addStretch(1)
        self.copyBtn.hide()
        if self.speakBtn is not None:
            self.speakBtn.hide()
        col.addWidget(self.acts)
        # 竖直固定：高度由 label 换行结果决定，不能被 feed 拉伸
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def _speak(self):
        """朗读本条回答；再点一次停止。"""
        try:
            import voice
        except Exception:
            return
        if getattr(self, "_speaking", False):
            voice.stop_speaking()
            self._speaking = False
            self.speakBtn.setText("朗读")
            return
        if voice.speak(self._raw):
            self._speaking = True
            self.speakBtn.setText("停止")
            QTimer.singleShot(1000, self._poll_speaking)

    def _poll_speaking(self):
        """播放结束后把按钮复位（winsound 无完成回调，按剩余时长轮询）。"""
        if not getattr(self, "_speaking", False):
            return
        try:
            import voice
            if voice.is_speaking():
                QTimer.singleShot(1000, self._poll_speaking)
                return
        except Exception:
            pass
        # 气泡可能在播放期间被删除（清空会话/停止生成），访问前必须防一手
        try:
            self._speaking = False
            if self.speakBtn is not None:
                self.speakBtn.setText("朗读")
        except RuntimeError:
            self._speaking = False

    def set_text(self, text):
        self._raw = text
        self.label.setText(format_body(self.role, text))

    def add_stats(self, text):
        """在消息时间戳后追加生成统计（tok/s、token 数等）。"""
        if hasattr(self, "timeLbl"):
            cur = self.timeLbl.text()
            self.timeLbl.setText((cur + " · " + text) if cur else text)

    def _fit_label(self, width):
        """锁定 QLabel 宽度并按换行后的真实高度撑开。

        中文没有空格，Qt 会把整句当成一个不可断的"词"：只设气泡宽度时文字会
        画出边界被裁掉；而只设 label 宽度又不会自动重算高度（表现为只显示一行）。
        所以宽度和高度都要显式给。
        """
        width = max(120, int(width))
        self.label.setFixedWidth(width)
        # 富文本 QLabel 的 heightForWidth() 不可靠（实测直接返回单行高度），
        # 用 QTextDocument 按同一宽度排版后取真实高度
        try:
            from PySide6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setDefaultFont(self.label.font())
            doc.setDocumentMargin(0)
            doc.setHtml(self.label.text())
            doc.setTextWidth(width)
            # 留 2% + 8px 余量：QSS 继承的字号/行高与 doc 默认字体可能有细微差异，
            # 算少了会把最后一行压在操作条上
            self.label.setFixedHeight(int(doc.size().height() * 1.02) + 8)
        except Exception:
            self.label.adjustSize()

    def set_max_width(self, px):
        if self.role == "ai":
            # AI 回答占满阅读宽度：只靠 sizeHint 会让每段文字缩成窄条
            self.bubble.setFixedWidth(px)
            self._fit_label(px - 20)                       # 16 左 + 4 右
            self.bubble.setFixedHeight(self.label.height() + 6)
        elif self.role == "me":
            # 用户气泡按最长一行估算宽度，短问题不拉满，长问题在 px 处换行
            fm = self.label.fontMetrics()
            lines = (self._raw or "").split("\n")
            ideal = max([fm.horizontalAdvance(x) for x in lines] or [0])
            w = max(180, min(int(px * 0.78), int(ideal * 1.12) + 40))
            self.bubble.setFixedWidth(w)
            self._fit_label(w - 26)
            self.bubble.setFixedHeight(self.label.height() + 23)
        elif getattr(self, "chip", None) is not None:
            # 提示条默认按 sizeHint 收缩，长文案会被折成很窄的一列（"正在录音…" 占三行）。
            # 按字数给个下限，短提示仍紧凑，长提示铺开到一两行。
            n = len(getattr(self, "_raw", "") or "")
            self.chip.setMaximumWidth(min(px, 560))
            self.chip.setMinimumWidth(max(160, min(px, 120 + 9 * n)))
            return
        self.bubble.adjustSize()
        self.updateGeometry()

    def _copy(self):
        QGuiApplication.clipboard().setText(self._raw)
        self.copyBtn.setIcon(make_icon("check", _icon_color("moss", "#15803D"), 13))
        self.copyBtn.setText("已复制")
        QTimer.singleShot(1400, self._reset_copy)

    def _copy_menu(self, pos):
        """右键复制菜单：Markdown 源码 / 纯文本。"""
        m = QMenu(self)
        a_md = m.addAction("复制 Markdown")
        a_plain = m.addAction("复制纯文本")
        act = m.exec(self.copyBtn.mapToGlobal(pos))
        if act not in (a_md, a_plain):
            return
        QGuiApplication.clipboard().setText(
            self._raw if act is a_md else _strip_markdown(self._raw))
        self.copyBtn.setIcon(make_icon("check", _icon_color("moss", "#15803D"), 13))
        self.copyBtn.setText("已复制")
        QTimer.singleShot(1400, self._reset_copy)

    def _reset_copy(self):
        self.copyBtn.setIcon(make_icon("copy", _icon_color("muted", "#5A6172"), 13))
        self.copyBtn.setText("复制")

    def enterEvent(self, e):
        super().enterEvent(e)
        if hasattr(self, "copyBtn"):
            self.copyBtn.show()
        if getattr(self, "speakBtn", None) is not None:
            self.speakBtn.show()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if hasattr(self, "copyBtn"):
            self.copyBtn.hide()
        if getattr(self, "speakBtn", None) is not None:
            self.speakBtn.hide()


# ==================== 模型选择浮层 ====================
class ModelRow(QWidget):
    """浮层里的一行模型：名称（等宽）+ 体积 + 当前项勾。"""

    picked = Signal(str)

    def __init__(self, name, size, checked, parent=None):
        super().__init__(parent)
        self.name = name
        self.setObjectName("modelRow")
        self.setAttribute(Qt.WA_StyledBackground, True)   # 让 #modelRow 的圆角/悬停生效
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(8)
        nm = QLabel(name)
        nm.setObjectName("popName")
        sz = QLabel(size or "")
        sz.setObjectName("popSize")
        h.addWidget(nm)
        h.addStretch(1)
        h.addWidget(sz)
        if checked:
            tick = QLabel("✓")
            tick.setObjectName("popTick")
            h.addWidget(tick)
        self._checked = checked

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.picked.emit(self.name)
        super().mouseReleaseEvent(e)


class ModelPopup(QFrame):
    """自定义模型浮层：比原生 QMenu 更贴近设计稿（圆角、行高、体积信息）。"""

    def __init__(self, parent, names, sizes, current, on_pick):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("modelPop")
        self._on_pick = on_pick
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(2)

        head = QLabel("本地模型 · 切换会自动释放上一个")
        head.setObjectName("popHead")
        v.addWidget(head)
        v.addSpacing(2)

        for n in names:
            row = ModelRow(n, sizes.get(n, ""), n == current, self)
            row.picked.connect(self._pick)
            v.addWidget(row)

        tip = QLabel("8B 及以上在 16G 机器上较慢，建议一次用一个。")
        tip.setObjectName("popTip")
        tip.setWordWrap(True)
        tip.setFixedWidth(280)
        v.addWidget(tip)
        self.setFixedWidth(300)

    def _pick(self, name):
        self.close()
        self._on_pick(name)


# ==================== 思考中指示 ====================
class ThinkingItem(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.avatar = QLabel()
        self.avatar.setFixedSize(30, 30)
        self.avatar.setPixmap(make_ai_avatar(30))
        self.bubble = QFrame()
        self.bubble.setObjectName("bubbleAI")
        bl = QHBoxLayout(self.bubble)
        bl.setContentsMargins(14, 11, 16, 11)
        bl.setSpacing(6)
        self.dots = [QLabel() for _ in range(3)]
        for d in self.dots:
            d.setFixedSize(7, 7)
            d.setObjectName("tdot")
            bl.addWidget(d, 0, Qt.AlignVCenter)
        self.txt = QLabel("正在思考…")
        self.txt.setObjectName("thinkTxt")
        bl.addWidget(self.txt)
        lay.addWidget(self.avatar)
        lay.addWidget(self.bubble)
        lay.addStretch(1)
        self._phase = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(180)
        self._apply()

    def _tick(self):
        self._phase = (self._phase + 1) % 3
        self._apply()

    def _apply(self):
        for i, d in enumerate(self.dots):
            if i == self._phase:
                d.setStyleSheet("#tdot{background:#3B5BDB;border-radius:4px;}")
            else:
                d.setStyleSheet("#tdot{background:#C7CEE0;border-radius:4px;}")

    def set_max_width(self, px):
        self.bubble.setMaximumWidth(px)

    def stop(self):
        self.timer.stop()


# ==================== 可自适应内容宽度的消息容器 ====================
class FeedWidget(QWidget):
    def __init__(self, on_resize=None, parent=None):
        super().__init__(parent)
        self.setObjectName("feed")
        self._on_resize = on_resize
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(20, 18, 22, 16)
        self.v.setSpacing(14)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._on_resize:
            self._on_resize()


# ==================== 生成参数对话框 ====================
class SettingsDialog(QDialog):
    def __init__(self, parent, temperature, num_ctx, system_prompt):
        super().__init__(parent)
        self.setWindowTitle("生成参数")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(QLabel("Temperature"))
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.5)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(temperature)
        row.addWidget(self.temp_spin)
        row.addSpacing(18)
        row.addWidget(QLabel("上下文"))
        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(2048, 8192)
        self.ctx_spin.setSingleStep(1024)
        self.ctx_spin.setValue(num_ctx)
        row.addWidget(self.ctx_spin)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("系统提示词（可选，留空则使用默认）"))
        self.sys_edit = QPlainTextEdit(system_prompt)
        self.sys_edit.setFixedHeight(110)
        self.sys_edit.setPlaceholderText("例如：你是严谨的技术助手，回答尽量简短。")
        lay.addWidget(self.sys_edit)
        tip = QLabel("提示：Qwen3 4B 在 4096 上下文下会输出乱码，请避开该值。")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)


# ==================== 主窗口 ====================
class ChatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("离线AI聊天 · 本地 Ollama")
        self.resize(980, 740)
        self.setMinimumSize(680, 520)
        self.setStyleSheet(build_qss())
        self._settings = QSettings("offline_chat", "offline_chat")
        self.model = self._settings.value("model") or "gemma3:4b"
        self.temperature = float(self._settings.value("temperature", 0.3))
        self.num_ctx = int(self._settings.value("num_ctx", 6144))
        self.system_prompt = str(self._settings.value("system_prompt", "") or "")
        # 深度思考：草稿 → 自检挑刺 → 定稿（三轮本地推理，质量换时间）
        self.deep = str(self._settings.value("deep", "false")).lower() in ("1", "true", "yes")
        # 长期记忆：默认开启，事实会作为系统提示的一部分随每轮发送
        self.use_memory = str(self._settings.value("use_memory", "true")).lower() in (
            "1", "true", "yes")
        # 主题：paper（墨绿纸感） / night（夜读）
        self.theme = str(self._settings.value("theme", "paper") or "paper")
        self._last_failed_text = ""
        self._pending_image = None       # 待发送图片（多模态）
        self._last_image = None          # 上次发送的图片，错误重试时自动带回
        self._pending_doc = None         # 待发送文档（PDF/Office/图片OCR）
        self._last_doc = None            # 上次发送的文档，错误重试时自动带回
        self._doc_question = ""          # 文档解析期间暂存本轮问题
        self._doc_worker = None          # 持有解析线程引用，防被 GC 提前回收
        self._cite_sources = []          # 最近一次文档问答的引用来源（[1][2] 可点）
        self._ocr_worker = None          # 图片 OCR 线程引用
        self._recording = False          # 语音录入中
        self._deep_stage = 0             # 深度思考进度：0 关闭 / 1 草稿 / 2 自检 / 3 定稿
        self._deep_draft = ""
        self._deep_crit = ""
        self._turn_extra = ""            # 本轮附加上下文（文档片段），深度思考后两轮要复用
        self._rag_idx = None             # 懒加载：文档检索索引（落在数据目录）
        self.setAcceptDrops(True)
        self.busy = False
        self.ai_item = None
        self.think_item = None
        self._bubbles = []
        self._welcome = None
        self._model_actions = []
        self.history = []              # [{"role": "user"/"assistant", "content": str}]
        self._loading = False          # 恢复会话期间禁止重复落盘
        _store().migrate_legacy(Path(_data_dir()) / "session.json")   # 旧单文件 → 多会话
        self.sid = str(self._settings.value("sid") or "") or _store().ensure(DEFAULT_SID)
        _CURRENT["sid"] = self.sid
        self._cancelled = False
        self._stopping = False         # 防 Esc 快捷键与 eventFilter 双触发
        self._pending_text = None      # 流式渲染节流缓冲
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self._flush_render)
        geo = self._settings.value("geometry")
        if geo:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        self.bridge = Bridge()
        self.bridge.status.connect(self._on_status)
        self.bridge.sys.connect(self._sys)
        self.bridge.models.connect(self._apply_models)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.error.connect(self._on_bg_error)
        self.bridge.voice_text.connect(self._on_voice_text)
        self._build()
        self._apply_theme()
        self._load_session()
        esc_sc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc_sc.activated.connect(self._stop_if_busy)
        clear_sc = QShortcut(QKeySequence("Ctrl+L"), self)
        clear_sc.activated.connect(self._clear_session)
        self._shortcuts = [esc_sc, clear_sc]      # 保持引用，防被回收
        threading.Thread(target=self._bootstrap, daemon=True).start()

    # ---------- 布局 ----------
    def _apply_theme(self):
        """切换/初始化主题：整窗重新套一份 QSS（子控件会一并继承）。"""
        global _THEME_TOKENS
        try:
            import theme
            tokens = dict(theme.THEMES.get(self.theme, theme.PAPER))
            _THEME_TOKENS = tokens
            self.setStyleSheet(theme.qss(self.theme))
            self._refresh_icons(tokens)
            if hasattr(self, "status"):
                self.status.retheme(tokens)
        except Exception as e:
            log("theme failed: %s" % e)

    def _refresh_icons(self, tokens):
        """图标是位图，QSS 改不了颜色 —— 换主题时必须按新令牌重画一遍。"""
        muted = tokens.get("muted", "#5F6B64")
        ink = tokens.get("ink", "#17211D")
        fg = tokens.get("send_fg", "#FFFFFF")
        for name, kind, color, size in (
                ("doc_btn", "doc", muted, 15),
                ("mic_btn", "mic", muted, 15),
                ("export_btn", "download", muted, 16),
                ("more_btn", "more", muted, 16),
                ("send_btn", "send", fg, 17),
                ("doc_icon", None, None, None)):
            if kind is None:
                continue
            w = getattr(self, name, None)
            if w is not None and hasattr(w, "setIcon"):
                w.setIcon(make_icon(kind, color, size))
        if hasattr(self, "doc_icon"):
            self.doc_icon.setPixmap(make_icon("doc", ink, 18).pixmap(QSize(18, 18)))
        for b in getattr(self, "_bubbles", []):
            try:
                b.copyBtn.setIcon(make_icon("copy", muted, 13))
            except Exception:
                pass

    def _toggle_theme(self, on: bool):
        self.theme = "night" if on else "paper"
        try:
            self._settings.setValue("theme", self.theme)
        except Exception:
            pass
        self._apply_theme()
        self._sys("已切换到%s" % ("夜读模式" if on else "纸感模式"))

    def _build(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main(), 1)
        self._refresh_session_btn()

    # ---------- 侧栏：品牌 / 新建 / 搜索 / 会话列表 / 记忆·设置·状态 ----------
    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(264)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 16, 14, 10)
        v.setSpacing(8)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.setContentsMargins(2, 0, 2, 6)
        seal = QLabel()
        seal.setObjectName("seal")
        seal.setFixedSize(26, 26)
        core = QLabel(seal)
        core.setObjectName("sealCore")
        core.setFixedSize(9, 9)
        core.move(7, 7)
        brand.addWidget(seal)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        bt.setContentsMargins(0, 0, 0, 0)
        nm = QLabel("离线")
        nm.setObjectName("brandName")
        sb = QLabel("LOCAL · NO CLOUD")
        sb.setObjectName("brandSub")
        bt.addWidget(nm)
        bt.addWidget(sb)
        brand.addLayout(bt)
        brand.addStretch(1)
        v.addLayout(brand)

        self.new_chat_btn = QPushButton("＋  新建对话")
        self.new_chat_btn.setObjectName("newChat")
        self.new_chat_btn.setCursor(Qt.PointingHandCursor)
        self.new_chat_btn.clicked.connect(self._new_session)
        v.addWidget(self.new_chat_btn)

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("搜索会话内容")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_sessions)
        v.addWidget(self.search)

        self.grp_label = QLabel("会话")
        self.grp_label.setObjectName("grpLabel")
        v.addWidget(self.grp_label)

        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionList")
        self.session_list.setFrameShape(QFrame.NoFrame)
        self.session_list.setSelectionMode(QListWidget.SingleSelection)
        self.session_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.session_list.itemClicked.connect(self._on_session_item)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._session_context_menu)
        v.addWidget(self.session_list, 1)

        foot = QFrame()
        foot.setObjectName("sideFoot")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(0, 8, 0, 0)
        fl.setSpacing(2)

        self.mem_btn = QPushButton("记忆")
        self.mem_btn.setObjectName("sideRow")
        self.mem_btn.setCursor(Qt.PointingHandCursor)
        self.mem_btn.setToolTip("长期记忆：让 AI 记住你的偏好与背景（存在本机 memory.md）")
        self.mem_menu = QMenu(self)
        self.mem_btn.setMenu(self.mem_menu)
        self.mem_menu.addAction("记住一条…", self._memory_add)
        self.mem_menu.addAction("查看全部", self._memory_view)
        self.mem_menu.addSeparator()
        act = QAction("启用记忆注入", self)
        act.setCheckable(True)
        act.setChecked(self.use_memory)
        act.triggered.connect(self._toggle_memory)
        self.mem_menu.addAction(act)
        self.mem_menu.addAction("忘掉一条…", self._memory_forget)
        fl.addWidget(self.mem_btn)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("sideRow")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setToolTip("生成参数：temperature / 上下文 / 系统提示词")
        self.settings_btn.clicked.connect(self._open_settings)
        fl.addWidget(self.settings_btn)

        # 会话管理（重命名 / 导出 / 删除仍走原来的菜单，逻辑不变）
        self.session_btn = QPushButton("会话管理")
        self.session_btn.setObjectName("sideRow")
        self.session_btn.setCursor(Qt.PointingHandCursor)
        self.session_btn.setToolTip("多会话：新建 / 切换 / 重命名 / 删除 / 导出 Markdown")
        self.session_menu = QMenu(self)
        self.session_btn.setMenu(self.session_menu)
        self.session_menu.aboutToShow.connect(self._build_session_menu)
        fl.addWidget(self.session_btn)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(6, 6, 0, 0)
        self.status = StatusPill()
        status_row.addWidget(self.status)
        status_row.addStretch(1)
        fl.addLayout(status_row)

        v.addWidget(foot)
        return side

    # ---------- 主区：顶栏 + 消息流 + 输入卡 ----------
    def _build_main(self):
        main = QWidget()
        main.setObjectName("mainCol")
        root = QVBoxLayout(main)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        hdr = QFrame()
        hdr.setObjectName("top")
        hdr.setFixedHeight(56)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(24, 0, 18, 0)
        hl.setSpacing(10)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        self.title_lbl = QLabel("离线AI聊天")
        self.title_lbl.setObjectName("title")
        self.sub_lbl = QLabel("本地会话 · 已保存")
        self.sub_lbl.setObjectName("sub")
        titles.addWidget(self.title_lbl)
        titles.addWidget(self.sub_lbl)
        hl.addLayout(titles)
        hl.addStretch(1)

        self.model_btn = QPushButton(self.model)
        self.model_btn.setObjectName("modelBtn")
        self.model_btn.setCursor(Qt.PointingHandCursor)
        self.model_menu = QMenu(self)       # 仅作为 action 容器（数据/测试用）
        self.model_btn.clicked.connect(self._show_model_popup)
        hl.addWidget(self.model_btn)

        self.export_btn = QPushButton()
        self.export_btn.setObjectName("iconBtn")
        self.export_btn.setIcon(make_icon("download", "#5F6B64", 16))
        self.export_btn.setIconSize(QSize(16, 16))
        self.export_btn.setFixedSize(32, 32)
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.setToolTip("导出当前会话为 Markdown")
        self.export_btn.clicked.connect(self._export_session)
        hl.addWidget(self.export_btn)

        self.more_btn = QPushButton()
        self.more_btn.setObjectName("iconBtn")
        self.more_btn.setIcon(make_icon("more", "#5F6B64", 16))
        self.more_btn.setIconSize(QSize(16, 16))
        self.more_btn.setFixedSize(32, 32)
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setToolTip("更多")
        more_menu = QMenu(self)
        more_menu.addAction("新建对话", self._new_session)
        more_menu.addAction("清空当前会话", self._clear_session)
        more_menu.addSeparator()
        self.theme_action = QAction("夜读模式", self)
        self.theme_action.setCheckable(True)
        self.theme_action.setChecked(self.theme == "night")
        self.theme_action.triggered.connect(self._toggle_theme)
        more_menu.addAction(self.theme_action)
        more_menu.addAction("设置…", self._open_settings)
        self.more_btn.setMenu(more_menu)
        self.more_menu = more_menu
        hl.addWidget(self.more_btn)

        root.addWidget(hdr)

        # 对话滚动区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setMaximumWidth(760)      # 真正的阅读列宽：滚动条贴内容
        self.feed = FeedWidget(self._recap)
        self.feed.v.setContentsMargins(0, 20, 0, 12)
        self.feed.v.setSpacing(18)
        # 单层布局（feed 直接做滚动内容），滚动区自身定宽并居中
        self.msg_layout = self.feed.v
        self.scroll.setWidget(self.feed)
        feed_row = QHBoxLayout()
        feed_row.setContentsMargins(0, 0, 0, 0)
        feed_row.addStretch(1)
        feed_row.addWidget(self.scroll, 10)
        feed_row.addStretch(1)
        root.addLayout(feed_row, 1)
        self._add_welcome()

        # 输入区
        wrap = QFrame()
        wrap.setObjectName("composerWrap")
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 6, 0, 10)
        wl.setSpacing(6)
        card_row = QHBoxLayout()
        card = QFrame()
        card.setObjectName("composerCard")
        card.setMaximumWidth(760)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.composer_card = card
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 12, 8)
        cl.setSpacing(6)

        # 待发送图片预览条（拖拽/粘贴后出现）
        self.image_chip = QFrame()
        self.image_chip.setObjectName("chip")
        il = QHBoxLayout(self.image_chip)
        il.setContentsMargins(8, 4, 8, 4)
        il.setSpacing(8)
        self.image_thumb = QLabel()
        self.image_thumb.setFixedSize(36, 36)
        self.image_name = QLabel()
        self.image_name.setObjectName("hint")
        self.ocr_btn = QPushButton("提取文字")
        self.ocr_btn.setObjectName("copyBtn")
        self.ocr_btn.setCursor(Qt.PointingHandCursor)
        self.ocr_btn.setToolTip("用本地 OCR 识别图片中的文字，结果填入输入框")
        self.ocr_btn.clicked.connect(self._ocr_image)
        self.image_remove = QPushButton("移除")
        self.image_remove.setObjectName("copyBtn")
        self.image_remove.setCursor(Qt.PointingHandCursor)
        self.image_remove.clicked.connect(self._clear_image)
        il.addWidget(self.image_thumb)
        il.addWidget(self.image_name)
        il.addStretch(1)
        il.addWidget(self.ocr_btn)
        il.addWidget(self.image_remove)
        self.image_chip.hide()
        cl.addWidget(self.image_chip)

        # 待发送文档预览条（拖拽文档 / 点"文档"按钮后出现）
        self.doc_chip = QFrame()
        self.doc_chip.setObjectName("chip")
        dl = QHBoxLayout(self.doc_chip)
        dl.setContentsMargins(8, 4, 8, 4)
        dl.setSpacing(8)
        self.doc_icon = QLabel()
        self.doc_icon.setPixmap(make_icon("doc", "#1F5C4A", 18).pixmap(QSize(18, 18)))
        self.doc_name = QLabel()
        self.doc_name.setObjectName("hint")
        self.doc_remove = QPushButton("移除")
        self.doc_remove.setObjectName("copyBtn")
        self.doc_remove.setCursor(Qt.PointingHandCursor)
        self.doc_remove.clicked.connect(self._clear_doc)
        dl.addWidget(self.doc_icon)
        dl.addWidget(self.doc_name)
        dl.addStretch(1)
        dl.addWidget(self.doc_remove)
        self.doc_chip.hide()
        cl.addWidget(self.doc_chip)

        self.input = QTextEdit()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("问点什么，或把文件拖进来…")
        self.input.setFixedHeight(56)      # 由 _autosize() 随内容调整
        self.input.setMinimumHeight(46)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.input.setAcceptRichText(False)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._on_input_changed)
        cl.addWidget(self.input)

        cbar = QHBoxLayout()
        cbar.setContentsMargins(2, 0, 0, 0)
        cbar.setSpacing(2)
        self.doc_btn = QPushButton("文档")
        self.doc_btn.setObjectName("copyBtn")
        self.doc_btn.setIcon(make_icon("doc", "#5F6B64", 15))
        self.doc_btn.setIconSize(QSize(15, 15))
        self.doc_btn.setCursor(Qt.PointingHandCursor)
        self.doc_btn.setToolTip("附加文档：PDF / Word / Excel / PPT / ePub / 图片\n"
                                "短文档直接随消息发送，长文档自动入库检索后作答")
        self.doc_btn.clicked.connect(self._pick_doc)
        cbar.addWidget(self.doc_btn)

        self.mic_btn = QPushButton("语音")
        self.mic_btn.setObjectName("copyBtn")
        self.mic_btn.setIcon(make_icon("mic", "#5F6B64", 15))
        self.mic_btn.setIconSize(QSize(15, 15))
        self.mic_btn.setCheckable(True)
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.setToolTip("语音输入：点一下开始录音，再点一下结束并转写（离线识别）")
        self.mic_btn.toggled.connect(self._toggle_record)
        self.mic_btn.setVisible(voice_stt_ok())
        cbar.addWidget(self.mic_btn)

        self.deep_btn = QPushButton("深度")
        self.deep_btn.setObjectName("copyBtn")
        self.deep_btn.setCheckable(True)
        self.deep_btn.setChecked(self.deep)
        self.deep_btn.setCursor(Qt.PointingHandCursor)
        self.deep_btn.setToolTip("深度思考：先出草稿 → 自我挑刺 → 重写定稿（约 3 倍耗时）")
        self.deep_btn.toggled.connect(self._toggle_deep)
        cbar.addWidget(self.deep_btn)

        cbar.addStretch(1)
        self.send_btn = QPushButton()
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(38, 32)
        self.send_btn.setIcon(make_icon("send", "#FFFFFF", 17))
        self.send_btn.setIconSize(QSize(17, 17))
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("发送（Enter）")
        self.send_btn.clicked.connect(self._on_send_clicked)
        self.send_btn.setEnabled(False)
        cbar.addWidget(self.send_btn)
        cl.addLayout(cbar)

        # 提示行单独放输入卡下方：原先挤在工具行右侧，会被压成一条
        card_row.addStretch(1)
        card_row.addWidget(card, 10)
        card_row.addStretch(1)
        wl.addLayout(card_row)

        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(4, 0, 6, 0)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self._set_hint_text()
        hint_row.addWidget(self.hint)
        hint_row.addStretch(1)
        wl.addLayout(hint_row)

        root.addWidget(wrap)
        return main

    # ---------- 会话列表（侧栏） ----------
    def _refresh_sessions(self):
        if not hasattr(self, "session_list"):
            return
        try:
            rows = _store().all()
        except Exception as e:
            log("refresh sessions failed: %s" % e)
            rows = []
        self.session_list.blockSignals(True)
        self.session_list.clear()
        if not rows:
            it = QListWidgetItem("暂无会话，点上方「新建对话」")
            it.setFlags(Qt.NoItemFlags)
            it.setSizeHint(QSize(0, 34))
            self.session_list.addItem(it)
            self.session_list.blockSignals(False)
            return
        today = time.strftime("%Y-%m-%d")
        yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        last_grp = None
        for r in rows:
            day = (r.get("updated") or "")[:10]
            grp = ("置顶" if r.get("pinned") else
                   ("今天" if day == today else ("昨天" if day == yday else "更早")))
            if grp != last_grp:
                gh = QListWidgetItem(grp)
                gh.setFlags(Qt.NoItemFlags)
                gh.setSizeHint(QSize(0, 28))
                gh.setData(Qt.UserRole + 1, "group")
                self.session_list.addItem(gh)
                last_grp = grp
            it = QListWidgetItem()
            it.setData(Qt.UserRole, r.get("id"))
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(2, 0, 2, 0)
            rl.setSpacing(1)
            t = QLabel((r.get("title") or "新会话")[:26])
            t.setObjectName("sessionTitle")
            meta_txt = "%s 条 · %s" % (r.get("count", 0),
                                       (r.get("updated") or "")[5:16])
            if r.get("pinned"):
                meta_txt = "置顶 · " + meta_txt
            m = QLabel(meta_txt)
            m.setObjectName("sessionMeta")
            rl.addWidget(t)
            rl.addWidget(m)
            it.setSizeHint(QSize(0, 50))
            it.setToolTip(t.text())
            self.session_list.addItem(it)
            self.session_list.setItemWidget(it, row)
            if r.get("id") == self.sid:
                self.session_list.setCurrentItem(it)
        self.session_list.blockSignals(False)

    def _session_context_menu(self, pos):
        """右键会话：重命名 / 导出 / 删除（对当前项，先切过去再接原逻辑）。"""
        it = self.session_list.itemAt(pos)
        if it is None or it.data(Qt.UserRole + 1) == "group":
            return
        sid = it.data(Qt.UserRole)
        if not sid:
            return
        menu = QMenu(self)
        row = _store().meta(sid)
        pin_label = "取消置顶" if row.get("pinned") else "置顶"
        menu.addAction(pin_label, lambda: self._toggle_pin(sid))
        menu.addSeparator()
        menu.addAction("重命名…", self._rename_session)
        menu.addAction("导出为 Markdown…", self._export_session)
        menu.addSeparator()
        menu.addAction("删除该会话", self._delete_session)
        if sid != self.sid:
            self._switch_session(sid)
        menu.exec(self.session_list.viewport().mapToGlobal(pos))

    def _toggle_pin(self, sid):
        cur = bool(_store().meta(sid).get("pinned"))
        if _store().set_pinned(sid, not cur):
            self._refresh_sessions()
            self._sys("已%s会话" % ("取消置顶" if cur else "置顶"))

    def _on_session_item(self, item):
        sid = item.data(Qt.UserRole)
        if sid and sid != self.sid:
            self._switch_session(sid)

    def _filter_sessions(self, text):
        text = (text or "").strip().lower()
        for i in range(self.session_list.count()):
            it = self.session_list.item(i)
            if it.data(Qt.UserRole + 1) == "group":
                it.setHidden(False)
                continue
            w = self.session_list.itemWidget(it)
            title = ""
            if w is not None:
                lab = w.findChild(QLabel, "sessionTitle")
                title = lab.text() if lab else ""
            it.setHidden(bool(text) and text not in title.lower())
        # 分组标题：其下会话全被过滤掉时一并隐藏
        n = self.session_list.count()
        for i in range(n - 1, -1, -1):
            it = self.session_list.item(i)
            if it.data(Qt.UserRole + 1) != "group":
                continue
            visible = False
            for j in range(i + 1, n):
                nxt = self.session_list.item(j)
                if nxt.data(Qt.UserRole + 1) == "group":
                    break
                if not nxt.isHidden():
                    visible = True
                    break
            it.setHidden(bool(text) and not visible)

    # ---------- 空状态 ----------
    def _add_welcome(self):
        if self._welcome is not None:
            return
        w = QFrame()
        w.setObjectName("welcome")
        w.setMaximumWidth(640)
        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        l = QVBoxLayout(w)
        l.setContentsMargins(6, 46, 6, 20)
        l.setSpacing(10)

        t = QLabel("问点什么，或者把文件丢进来。")
        t.setObjectName("wTitle")
        l.addWidget(t)
        s = QLabel("完全本地推理 · 断网可用 · 文档/图片/语音都能处理")
        s.setObjectName("wSub")
        l.addWidget(s)
        l.addSpacing(18)

        starters = [
            ("读一份文档", "把这篇文章的结论整理成三点"),
            ("解释一个概念", "用小学生能懂的话解释什么是 RFS 多目标跟踪"),
            ("起草一段文字", "帮我写一封请假邮件，语气正式一点"),
        ]
        for title, prompt in starters:
            b = QPushButton("%s　·　%s" % (title, prompt))
            b.setObjectName("starter")
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(44)
            b.clicked.connect(lambda _=False, x=prompt: self._use_suggestion(x))
            l.addWidget(b)

        l.addSpacing(6)
        hint = QLabel("支持 PDF / Word / Excel / PPT / ePub / 图片　·　拖进来即可")
        hint.setObjectName("wStatus")
        l.addWidget(hint)

        self.msg_layout.addWidget(w, 0, Qt.AlignHCenter)
        self._welcome = w

    def _remove_welcome(self):
        if self._welcome is not None:
            self.msg_layout.removeWidget(self._welcome)
            # 只 removeWidget 的话 widget 仍挂在 feed 上，deleteLater 之前会继续
            # 按旧几何位置绘制 —— 表现为"欢迎卡压在对话上面"
            self._welcome.hide()
            self._welcome.setParent(None)
            self._welcome.deleteLater()
            self._welcome = None

    def _use_suggestion(self, text):
        self.input.setPlainText(text)
        self.input.setFocus()
        self._autosize()

    # ---------- 消息增删 ----------
    def _align_for(self, role):
        if role == "me":
            return Qt.AlignRight
        # 系统/错误提示改为左对齐内联卡片：居中窄条会把长文案折成一列
        return Qt.AlignLeft

    def _add_message(self, role, text, who="", ts="", action=None):
        item = BubbleItem(role, text, who, ts, action=action,
                          on_cite=self._show_cite if role == "ai" else None)
        self.msg_layout.addWidget(item, 0, self._align_for(role))
        if role in ("ai", "me"):
            self._bubbles.append(item)
        self._recap()
        self._scroll_bottom()
        return item

    def _add_thinking(self):
        item = ThinkingItem()
        self.msg_layout.addWidget(item, 0, Qt.AlignLeft)
        self._bubbles.append(item)
        self._recap()
        self._scroll_bottom()
        return item

    def _remove_item(self, item):
        if item in self._bubbles:
            self._bubbles.remove(item)
        self.msg_layout.removeWidget(item)
        if item is self.think_item:
            item.stop()
        item.deleteLater()

    def _recap(self):
        fw = self.feed.width()
        if fw <= 0:
            return
        mw = max(240, min(fw - 8, 740))
        lay = self.msg_layout
        for i in range(lay.count()):
            it = lay.itemAt(i).widget()
            # 遍历布局而不是只遍历 _bubbles：sys/error 提示条也要参与宽度收敛
            if it is not None and hasattr(it, "set_max_width"):
                it.set_max_width(mw)

    def _at_bottom(self):
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 48

    def _scroll_bottom(self, force=True):
        """force=False 时仅在用户本来就贴着底部才跟随，避免上翻历史被反复拽回。"""
        if not force and not self._at_bottom():
            return
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar()
                          .setValue(self.scroll.verticalScrollBar().maximum()))

    @staticmethod
    def _now():
        import datetime
        return datetime.datetime.now().strftime("%H:%M")

    # ---------- 发送 / 流式 ----------
    def _ctx_tokens(self):
        """粗略估算多轮上下文 token 数（中英混合约 1.6 字符/token）。"""
        chars = len(self.system_prompt) + sum(len(m.get("content", "")) for m in self.history)
        return int(chars / 1.6)

    def _set_hint_text(self):
        if not hasattr(self, "hint"):
            return
        extra = " · 深度思考" if getattr(self, "deep", False) else ""
        self.hint.setText(
            "Enter 发送 · Shift+Enter 换行 · Esc 停止 · Ctrl+L 清空　|　"
            "%s · T%.1f%s · 上下文 ~%d/%d"
            % (self.model, self.temperature, extra, self._ctx_tokens(), self.num_ctx))

    # ---------- 深度思考：草稿 → 自检 → 定稿 ----------
    def _toggle_deep(self, on: bool):
        self.deep = bool(on)
        try:
            self._settings.setValue("deep", "true" if on else "false")
        except Exception:
            pass
        self._set_hint_text()
        if on:
            self._add_message("sys", "已开启深度思考：每轮会先出草稿、再自检挑刺、最后重写定稿"
                                     "（耗时约为普通的 3 倍）")

    def _begin_turn(self, messages):
        """开始一轮对话；开启深度思考时这只是第 1 步（草稿）。"""
        self._deep_stage = 1 if self.deep else 0
        self._deep_draft = ""
        self._deep_crit = ""
        self._start_worker(messages)

    def _deep_prompts(self, stage: int) -> list:
        """组装第 2/3 步的消息链：把草稿与自检意见作为上下文接在原始对话后面。"""
        msgs = self._build_messages()
        # 文档问答场景：_build_messages 只带历史文本，文档片段必须补回来，
        # 否则自检/定稿两轮根本看不到原文，只能凭草稿瞎改
        if self._turn_extra and msgs and msgs[-1].get("role") == "user":
            msgs[-1]["content"] = "%s\n\n%s" % (msgs[-1]["content"], self._turn_extra)
        msgs.append({"role": "assistant", "content": self._deep_draft})
        if stage == 2:
            msgs.append({"role": "user", "content": DEEP_CRITIQUE})
        else:
            msgs.append({"role": "user", "content": self._deep_crit})
            msgs.append({"role": "assistant", "content": self._deep_crit})
            msgs.append({"role": "user", "content": DEEP_REVISE})
        if self.model.lower().startswith("qwen3"):
            msgs[-1]["content"] = "/no_think\n" + msgs[-1]["content"]
        return msgs

    def _deep_capture(self, text):
        """收起中间产物（不进历史），只留一条提示条，避免上下文被草稿撑爆。"""
        if self.ai_item is not None:
            self._remove_item(self.ai_item)
            self.ai_item = None
        if self._deep_stage == 1:
            self._deep_draft = text
            self._add_message("sys", "① 草稿完成（%d 字），正在自检…" % len(text))
        else:
            self._deep_crit = text
            preview = (text or "").strip().replace("\n", " ")[:220]
            self._add_message("sys", "② 自检意见：%s" % (preview or "（无具体意见）"))

    def _deep_advance(self):
        nxt = 2 if self._deep_stage == 1 else 3
        msgs = self._deep_prompts(nxt)
        self._deep_stage = nxt
        self.think_item = self._add_thinking()
        self._start_worker(msgs)

    def _on_input_changed(self):
        self._autosize()
        # 生成中按钮是"停止"，必须保持可点
        self.send_btn.setEnabled(self.busy or bool(self.input.toPlainText().strip()))

    def _autosize(self):
        h = self.input.document().size().height()
        self.input.setFixedHeight(max(46, min(120, int(h) + 12)))

    def _on_send_clicked(self):
        if self.busy:
            self._stop()
        else:
            self._send()

    def _set_busy_ui(self, busy):
        if busy:
            self.send_btn.setIcon(make_icon("stop", "#FFFFFF", 16))
            self.send_btn.setIconSize(QSize(16, 16))
            self.send_btn.setToolTip("停止生成 (Esc)")
            self.send_btn.setEnabled(True)
        else:
            self.send_btn.setIcon(make_icon("send", "#FFFFFF", 18))
            self.send_btn.setIconSize(QSize(18, 18))
            self.send_btn.setToolTip("发送")
            self.send_btn.setEnabled(bool(self.input.toPlainText().strip()))

    def _build_messages(self):
        """按当前系统提示词与历史组装请求；/no_think 只作用于当前轮用户消息。"""
        msgs = []
        sys_text = self.system_prompt[:2000]
        note = self._memory_note()          # 长期记忆：已知的偏好/事实
        if note:
            sys_text = (note + "\n" + sys_text).strip()
        if sys_text:
            msgs.append({"role": "system", "content": sys_text})
        msgs.extend(dict(m) for m in self.history)
        if (msgs and msgs[-1].get("role") == "user"
                and self.model.lower().startswith("qwen3")):
            msgs[-1]["content"] = "/no_think\n" + msgs[-1]["content"]
        return msgs

    def _send(self):
        if self.busy:
            return
        text = self.input.toPlainText().strip()
        if not text and not self._pending_image and not self._pending_doc:
            return
        free = _free_mem_gb()
        if 0 < free < 1.6 and time.time() - getattr(self, "_mem_hint_ts", 0) > 600:
            self._mem_hint_ts = time.time()
            self._sys("可用内存仅剩 %.1f GB：大模型可能加载失败或输出异常，"
                      "建议先关掉一些程序再试。" % free)
        doc_name = os.path.basename(self._pending_doc) if self._pending_doc else ""
        if not text:
            text = "请看这张图片。" if self._pending_image else "请总结这份文档的内容。"
        img_b64 = None
        img_name = ""
        if self._pending_image:
            try:
                with open(self._pending_image, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("ascii")
                img_name = os.path.basename(self._pending_image)
            except Exception as e:
                self._add_message("error", "读取图片失败：%s" % e)
                return
        self._remove_welcome()
        self.input.clear()
        self._autosize()
        tag = ("\n［图片：%s］" % img_name if img_name
              else "\n［文档：%s］" % doc_name if doc_name else "")
        self._add_message("me", text + tag, who="你", ts=self._now())
        self.history.append({"role": "user", "content": text})
        self._last_failed_text = text
        self._trim_history()
        self.busy = True
        self._cancelled = False
        self._set_busy_ui(True)
        self._set_hint_text()
        self.think_item = self._add_thinking()
        self._turn_extra = ""              # 普通轮次不携带文档上下文
        if self._pending_doc:
            self._start_doc_flow(text)     # 先解析文档，再交给模型
            return
        messages = self._build_messages()
        if img_b64:
            messages[-1]["images"] = [img_b64]
        self._last_image = self._pending_image
        self._clear_image()
        self._begin_turn(messages)
        self._save_session()

    def _start_worker(self, messages):
        self.worker = Worker(self.model, messages, self.temperature, self.num_ctx)
        self.worker.token.connect(self._on_token)
        self.worker.finished.connect(self._finish)
        self.worker.errored.connect(self._on_error)
        self.worker.stats.connect(self._on_stats)
        self.worker.start()

    # ---------- 文档问答（解析 → 入库检索 → 作答） ----------
    def _doc_index(self):
        """文档检索索引：落在数据目录，不污染用户文件夹。"""
        if self._rag_idx is None:
            from server.rag import RagIndex
            self._rag_idx = RagIndex(_data_dir())
        return self._rag_idx

    def _start_doc_flow(self, question):
        path = self._pending_doc
        self._last_doc = path
        self._doc_question = question
        self._clear_doc()
        self._last_image = None
        w = DocWorker(path, ocr=True)
        w.done.connect(self._on_doc_parsed)
        w.failed.connect(self._on_doc_failed)
        self._doc_worker = w                # 必须持有引用：局部变量的 QThread 会被 GC 销毁
        w.start()
        self._save_session()

    def _abort_doc_turn(self):
        """解析期间被停止：撤掉思考气泡与本轮用户消息，恢复可输入状态。"""
        if self.think_item is not None:
            self._remove_item(self.think_item)
            self.think_item = None
        if self.history and self.history[-1].get("role") == "user":
            self.history.pop()
        for b in reversed(self._bubbles):
            if getattr(b, "role", "") == "me":
                b.in_history = False
                break
        self.busy = False
        self._cancelled = False
        self._stopping = False
        self._set_busy_ui(False)
        self._set_hint_text()

    def _on_doc_parsed(self, path, text):
        if not self.busy:
            return                          # 期间已被清空会话
        if self._cancelled:                 # 解析期间点了"停止"
            self._abort_doc_turn()
            return
        name = os.path.basename(path)
        q = self._doc_question or ""
        if len(text) <= self.DOC_INLINE_CHARS:
            self._sys("已解析《%s》：%d 字，随消息一起发送" % (name, len(text)))
            block = ("以下是文档《%s》的全文，请据此回答；"
                     "如果文档里没有答案，请明确说明。\n\n%s" % (name, text))
        else:
            block = self._doc_retrieve_block(path, text, q, name)
        messages = self._build_messages()
        if messages and messages[-1].get("role") == "user":
            messages[-1]["content"] = "%s\n\n%s" % (messages[-1]["content"], block)
        else:
            messages.append({"role": "user", "content": block})
        self._turn_extra = block
        self._begin_turn(messages)

    def _doc_retrieve_block(self, path, text, question, name):
        """长文档：单文件入库 → 检索相关片段 → 拼成带 [1][2] 引用的上下文。"""
        try:
            idx = self._doc_index()
            key = idx.key_for(path)
            idx.add_text(key, text)
            hits = idx.query(question or "文档内容概要", top_k=3, paths=[key])
        except Exception as e:
            hits = []
            self._sys("检索不可用，改用全文前段：%s" % e)
        self._cite_sources = []
        if hits:
            self._cite_sources = [
                {"path": name, "page": h.get("path", ""), "text": h.get("text", "")}
                for h in hits]
            self._sys("已解析《%s》：%d 字，命中 %d 个相关片段"
                      % (name, len(text), len(hits)))
            ctx = "\n\n".join("资料%d：\n%s" % (i, h["text"])
                              for i, h in enumerate(hits, 1))
            return ("以下是文档《%s》中与问题最相关的片段，请只据此回答，"
                    "并用 [1][2] 标注引用；资料不足时明确说明。\n\n%s" % (name, ctx))
        self._sys("已解析《%s》：%d 字（未命中片段，取前 6000 字）"
                  % (name, len(text)))
        return ("以下是文档《%s》的开头部分，请据此回答；"
                "若信息不足请说明。\n\n%s" % (name, text[:6000]))

    def _show_cite(self, num):
        """点击 [n]：在鼠标旁显示来源文件名与片段。"""
        try:
            i = int(num) - 1
            src = (getattr(self, "_cite_sources", []) or [])[i]
        except Exception:
            QToolTip.showText(QCursor.pos(), "找不到该引用来源", self)
            return
        text = (src.get("text") or "").strip().replace("\n", " ")
        if len(text) > 220:
            text = text[:220] + "…"
        QToolTip.showText(
            QCursor.pos(), "《%s》\n%s" % (src.get("path", ""), text), self,
            QRect(), 12000)

    def _on_doc_failed(self, path, err):
        if not self.busy:
            return
        self._on_error("解析文档《%s》失败：%s" % (os.path.basename(path), err))

    def _trim_history(self, max_chars=3200):
        """按字符预算裁剪最旧的历史（保留最后一轮），防上下文溢出 6144。"""
        total = sum(len(m.get("content", "")) for m in self.history)
        while total > max_chars and len(self.history) > 1:
            total -= len(self.history.pop(0).get("content", ""))

    def _on_token(self, buf):
        if self.think_item is not None:
            self._remove_item(self.think_item)
            self.think_item = None
        if self.ai_item is None:
            self.ai_item = self._add_message("ai", buf + "▍", who="AI", ts=self._now())
            self._pending_text = None
        else:
            # 节流：token 高频到达时合并到 80ms 一次渲染，长回答不再逐 token 重排
            self._pending_text = buf + "▍"      # 生成中的光标（定稿时会被覆盖）
            if not self._render_timer.isActive():
                self._render_timer.start()

    def _flush_render(self):
        if self._pending_text is not None and self.ai_item is not None:
            self.ai_item.set_text(self._pending_text)
            self._pending_text = None
            self._scroll_bottom(force=False)

    def _on_stats(self, st):
        if self.ai_item is None:
            return
        n = int(st.get("eval_count") or 0)
        dur = (st.get("eval_duration") or 0) / 1e9
        ptok = int(st.get("prompt_eval_count") or 0)
        parts = []
        if n:
            parts.append("%d tokens" % n)
        if n and dur > 0:
            parts.append("%.1f tok/s" % (n / dur))
        if ptok:
            parts.append("输入 %d" % ptok)
        if parts:
            self.ai_item.add_stats(" · ".join(parts))

    def _stop_if_busy(self):
        if self.busy:
            self._stop()

    def _stop(self):
        if not self.busy or self._stopping:
            return
        self._stopping = True
        self._cancelled = True
        try:
            self.worker.cancel()
        except Exception:
            pass
        self._add_message("sys", "已停止生成")
        # busy 状态等 finished 信号统一收尾，避免与 worker 竞争

    def _warn_degenerate(self, bad: str):
        """模型输出退化：丢掉垃圾内容，给出可操作建议（而不是让用户以为软件坏了）。"""
        head = (bad or "").strip()[:20].replace("\n", " ")
        self._add_message(
            "error",
            "模型「%s」这次没有正常输出（返回了重复字符：%s…），结果已丢弃、未存入会话。\n"
            "最常见原因是**内存被多个模型占满**（本机 16GB，两个模型同时驻留就会这样）。处理：\n"
            "① 换一个模型试（程序会自动卸载上一个模型，通常立刻恢复）；\n"
            "② 关掉其他大程序（浏览器多标签、PS、IDE 等）后重试；\n"
            "③ 到「设置」把上下文改回 6144 再试。"
            % (self.model, head))

    def _finish(self):
        # 空响应（无 token）时也要移除思考气泡，否则"正在思考…"永久残留
        if self.think_item is not None:
            self._remove_item(self.think_item)
            self.think_item = None
        # 冲刷最后一批未渲染的 token
        if self._pending_text is not None and self.ai_item is not None:
            self.ai_item.set_text(self._pending_text)
            self._pending_text = None
        text = getattr(getattr(self, "worker", None), "result_text", "")
        # 去掉生成中的光标；顺手同步到气泡（否则定稿后气泡里还留着 ▍）
        if text.endswith("▍"):
            text = text[:-1]
        if self.ai_item is not None and text:
            self.ai_item.set_text(text)
        if self._deep_stage in (1, 2):
            # 中间产物不进历史：否则草稿+自检会把下一轮的上下文撑爆
            self._deep_capture(text)
            if self._cancelled:                    # 用户中途停止：就此收尾
                self._deep_stage = 0
            else:
                self._deep_advance()
                return
        # 模型吐"0000000"或乱码时，不要把垃圾当答案写进历史/会话
        if text and not self._cancelled and degenerate_output(text):
            self._warn_degenerate(text)
            if self.ai_item is not None:
                self._remove_item(self.ai_item)
                self.ai_item = None
            text = ""
        if self.ai_item is not None and text:
            self.history.append({"role": "assistant", "content": text})
        self._deep_stage = 0
        self._turn_extra = ""
        self.ai_item = None
        self.busy = False
        self._cancelled = False
        self._stopping = False
        self._set_busy_ui(False)
        self._set_hint_text()
        self._save_session()
        self._scroll_bottom(force=False)

    def _on_error(self, msg):
        if self.think_item is not None:
            self._remove_item(self.think_item)
            self.think_item = None
        # 失败时丢弃不完整的回答气泡：否则下次启动恢复会话时，
        # 半截回答会被当成完整助手消息写进上下文。
        if self.ai_item is not None:
            self._remove_item(self.ai_item)
            self.ai_item = None
        self._pending_text = None
        # 失败轮的用户气泡保留展示，但标记为不进上下文（重载会话也不会重复携带）
        for b in reversed(self._bubbles):
            if getattr(b, "role", "") == "me":
                b.in_history = False
                break
        # 本轮没有产出，回滚刚加入的用户消息，避免下一轮重复携带
        if self.history and self.history[-1].get("role") == "user":
            self.history.pop()
        # 深度思考中途失败：丢弃已完成的步骤，重试时从头再来
        self._deep_stage = 0
        self._deep_draft = ""
        self._deep_crit = ""
        self._turn_extra = ""
        action = None
        if "multimodal" in msg.lower():
            msg = ("当前模型不支持图像输入（缺少视觉投影文件）。请切换到带视觉能力的模型"
                   "（如 gemma3:4b-vision），或改用文字描述图片内容。")
        elif self._last_failed_text:
            action = ("重试", self._retry_last)
        self._add_message("error", msg, action=action)
        self.busy = False
        self._cancelled = False
        self._stopping = False
        self._set_busy_ui(False)
        self._set_hint_text()

    def _retry_last(self):
        """错误后一键重试：回填失败输入（含上次的图片）并重新发送。"""
        if self.busy or not self._last_failed_text:
            return
        if self._last_doc and os.path.isfile(self._last_doc):
            self._attach_doc(self._last_doc)
        elif self._last_image and os.path.isfile(self._last_image):
            self._attach_image(self._last_image)
        self.input.setPlainText(self._last_failed_text)
        self._autosize()
        self._send()

    def _apply_settings(self, temperature, num_ctx, system_prompt):
        """应用生成参数；qwen3 + 4096 会自动纠正，返回是否纠正过。"""
        self.temperature = float(temperature)
        self.num_ctx = int(num_ctx)
        # 系统提示词过长会挤占上下文（前端截断会让模型丢指令），限制在 2000 字符
        self.system_prompt = (system_prompt or "").strip()[:2000]
        corrected = self.num_ctx == 4096 and self.model.lower().startswith("qwen3")
        if corrected:
            self.num_ctx = 6144
        return corrected

    def _open_settings(self):
        dlg = SettingsDialog(self, self.temperature, self.num_ctx, self.system_prompt)
        if dlg.exec() != QDialog.Accepted:
            return
        corrected = self._apply_settings(dlg.temp_spin.value(), dlg.ctx_spin.value(),
                                         dlg.sys_edit.toPlainText())
        for k, v in (("temperature", self.temperature), ("num_ctx", self.num_ctx),
                     ("system_prompt", self.system_prompt)):
            try:
                self._settings.setValue(k, v)
            except Exception:
                pass
        self._set_hint_text()
        note = ("参数已更新：temperature=%.1f · 上下文=%d"
                % (self.temperature, self.num_ctx))
        if corrected:
            note += "（qwen3 不支持 4096，已自动改为 6144）"
        self._add_message("sys", note)

    # ---------- 图片输入（多模态） ----------
    _IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")
    _DOC_EXTS = (".pdf", ".docx", ".xlsx", ".pptx", ".epub",
                 ".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".py")
    DOC_INLINE_CHARS = 3000      # 短于此值直接随消息发送，超过则走检索作答

    def _droppable(self, mime):
        """从拖入的 URL 里挑出第一个可用文件，返回 (图片路径, 文档路径)。"""
        if mime is None or not mime.hasUrls():
            return None, None
        for u in mime.urls():
            p = u.toLocalFile()
            if not p or not os.path.isfile(p):
                continue
            low = p.lower()
            if low.endswith(self._IMG_EXTS):
                return p, None
            if low.endswith(self._DOC_EXTS):
                return None, p
        return None, None

    def dragEnterEvent(self, e):
        img, doc = self._droppable(e.mimeData())
        if img or doc:
            e.acceptProposedAction()

    def dropEvent(self, e):
        img, doc = self._droppable(e.mimeData())
        if img:
            self._attach_image(img)
            e.acceptProposedAction()
        elif doc:
            self._attach_doc(doc)
            e.acceptProposedAction()

    def _pick_doc(self):
        dp = docparse_module()
        if dp is None:
            self._add_message("error", "文档解析组件不可用（打包时未包含 server.docparse）")
            return
        exts = sorted(set(dp.SUPPORTED_EXTS) | set(dp.OCR_EXTS))
        pattern = "文档 (%s)" % " ".join("*" + e for e in exts)
        path, _ = QFileDialog.getOpenFileName(self, "选择文档", "", pattern)
        if path:
            self._attach_doc(path)

    def _attach_doc(self, path):
        try:
            if os.path.getsize(path) > 50 * 1024 * 1024:
                self._add_message("error", "文档过大（上限 50MB）：%s"
                                  % os.path.basename(path))
                return
            self._pending_doc = path
            self._clear_image()            # 图片与文档互斥，避免一次请求塞两种附件
            self.doc_name.setText(os.path.basename(path))
            self.doc_chip.show()
            self.input.setFocus()
        except Exception as e:
            self._add_message("error", "附加文档失败：%s" % e)

    def _clear_doc(self):
        self._pending_doc = None
        self.doc_chip.hide()

    def _attach_image(self, path):
        try:
            self._clear_doc()              # 与文档互斥
            if os.path.getsize(path) > 20 * 1024 * 1024:
                self._add_message("error", "图片过大（上限 20MB）")
                return
            pm = QPixmap(path)
            if pm.isNull():
                self._add_message("error", "无法读取图片：%s" % os.path.basename(path))
                return
            self._pending_image = path
            self.image_thumb.setPixmap(pm.scaled(40, 40, Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))
            self.image_name.setText(os.path.basename(path))
            self.image_chip.show()
            self.input.setFocus()
        except Exception as e:
            self._add_message("error", "附加图片失败：%s" % e)

    def _clear_image(self):
        self._pending_image = None
        self.image_chip.hide()
        self.image_thumb.clear()
        self._ocr_btn_reset()

    # ---------- 长期记忆 ----------
    def _memory_note(self) -> str:
        """把长期记忆拼进系统提示词；未开启或为空则返回空串。"""
        if not self.use_memory:
            return ""
        try:
            from server.memory import as_system_note
            return as_system_note(_data_dir(), top_k=8)
        except Exception:
            return ""

    def _memory_add(self):
        from PySide6.QtWidgets import QInputDialog
        fact, ok = QInputDialog.getText(
            self, "记住一条", "要记住什么？（如：回答尽量简短 / 我在做离线AI项目）")
        if not ok or not (fact or "").strip():
            return
        try:
            from server.memory import remember
            self._sys(remember(_data_dir(), fact.strip()))
        except Exception as e:
            self._add_message("error", "写入记忆失败：%s" % e)

    def _toggle_memory(self, on: bool):
        self.use_memory = bool(on)
        try:
            self._settings.setValue("use_memory", "true" if on else "false")
        except Exception:
            pass
        self._sys("长期记忆注入已%s" % ("开启" if on else "关闭"))

    def _memory_view(self):
        try:
            from server.memory import recall
            txt = recall(_data_dir(), "", top_k=50)
        except Exception as e:
            txt = "读取失败：%s" % e
        self._sys("长期记忆：\n" + txt)

    def _memory_forget(self):
        from PySide6.QtWidgets import QInputDialog
        kw, ok = QInputDialog.getText(self, "忘掉一条", "输入要删除的关键词：")
        if not ok or not (kw or "").strip():
            return
        try:
            from server.memory import forget
            self._sys(forget(_data_dir(), kw.strip()))
        except Exception as e:
            self._add_message("error", "删除失败：%s" % e)

    # ---------- 多会话 ----------
    def _session_title(self) -> str:
        m = _store().meta(self.sid)
        return (m.get("title") or "新会话")[:12]

    def _refresh_session_btn(self):
        title = self._session_title()
        if hasattr(self, "session_btn"):
            self.session_btn.setText("会话管理")
        if hasattr(self, "title_lbl"):
            self.title_lbl.setText(title)
        self._refresh_sessions()

    def _build_session_menu(self):
        menu = self.session_menu
        menu.clear()
        menu.addAction("新建会话", self._new_session)
        menu.addSeparator()
        rows = _store().all()[:20]
        if not rows:
            menu.addAction("（暂无会话）").setEnabled(False)
        for r in rows:
            act = QAction("%s　%s 条" % (r.get("title") or "新会话", r.get("count", 0)), self)
            act.setCheckable(True)
            act.setChecked(r.get("id") == self.sid)
            act.triggered.connect(lambda _=False, s=r.get("id"): self._switch_session(s))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("重命名当前会话…", self._rename_session)
        menu.addAction("导出为 Markdown…", self._export_session)
        menu.addAction("删除当前会话", self._delete_session)

    def _switch_session(self, sid):
        if not sid or sid == self.sid or self.busy:
            return
        self._save_session()
        self.sid = sid
        _CURRENT["sid"] = sid
        try:
            self._settings.setValue("sid", sid)
        except Exception:
            pass
        self._loading = True
        self._cite_sources = []
        try:
            self._clear_feed()
            self.history.clear()
            self._load_session()
            if not self._bubbles:
                self._add_welcome()
        finally:
            self._loading = False
        self._refresh_session_btn()
        self._set_hint_text()

    def _new_session(self):
        if self.busy:
            return
        self._save_session()
        self._switch_session(_store().create("新会话"))

    def _rename_session(self):
        from PySide6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(self, "重命名会话", "会话名称：",
                                         text=_store().meta(self.sid).get("title", ""))
        if ok and title.strip():
            _store().rename(self.sid, title.strip())
            self._save_session()
            self._refresh_session_btn()

    def _export_session(self):
        from PySide6.QtWidgets import QFileDialog
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出会话", "%s.md" % self._session_title(), "Markdown (*.md)")
        if not dest:
            return
        try:
            self._save_session()
            p = _store().export_md(self.sid, dest)
            self._sys("已导出到 %s" % p)
        except Exception as e:
            self._add_message("error", "导出失败：%s" % e)

    def _delete_session(self):
        from PySide6.QtWidgets import QMessageBox
        if self._session_title() and len(_store().all()) <= 1:
            self._add_message("sys", "只剩这一个会话了；要重置内容请用 Ctrl+L 清空。")
            return
        if QMessageBox.question(self, "删除会话", "删除当前会话及其历史？") != QMessageBox.Yes:
            return
        _store().delete(self.sid)
        rows = _store().all()
        self.sid = rows[0]["id"] if rows else _store().create("新会话")
        _CURRENT["sid"] = self.sid
        try:
            self._settings.setValue("sid", self.sid)
        except Exception:
            pass
        self._loading = True
        self._cite_sources = []
        try:
            self._clear_feed()
            self.history.clear()
            self._load_session()
            if not self._bubbles:
                self._add_welcome()
        finally:
            self._loading = False
        self._refresh_session_btn()

    # ---------- 语音输入（本地 whisper） ----------
    def _toggle_record(self, on: bool):
        if on:
            self._start_record()
        else:
            self._stop_record()

    def _start_record(self):
        if self.busy:
            self.mic_btn.setChecked(False)
            return
        if getattr(self, "_recording", False):
            return
        try:
            import voice
            voice.stop_speaking()          # 录音时不念东西，避免自激
        except Exception:
            pass
        # 没有可用麦克风就别启动线程：直接把可操作的原因说清楚，
        # 而不是让 PortAudio 抛 "Error querying device -1"
        try:
            import voice
            if not voice.has_input_device():
                self.mic_btn.setChecked(False)
                self._add_message("error", voice.NO_MIC_TIP)
                return
        except Exception:
            pass
        self._recording = True
        self._sys("正在录音…（再点一次麦克风结束并识别）")
        threading.Thread(target=self._record_worker, daemon=True).start()

    def _stop_record(self):
        self._recording = False            # 收音循环到点自然结束

    def _record_worker(self):
        """后台录音 + 转写；结果经 Bridge 信号回主线程。"""
        try:
            import voice
            audio = voice.record_until(lambda: not getattr(self, "_recording", False))
            if len(audio) < voice.SAMPLE_RATE // 3:      # 短于 0.3 秒视作没录到
                audio = None
            if audio is not None and len(audio):
                _emit(self.bridge.sys, "正在识别语音…")
                text = voice.transcribe(audio)
                _emit(self.bridge.voice_text, text or "")
            else:
                _emit(self.bridge.sys, "（没有录到声音）")
        except Exception as e:
            _emit(self.bridge.error, "语音输入失败：%s" % e)
        finally:
            self._recording = False
            _emit(self.bridge.status, "ready", "")

    def _on_bg_error(self, msg: str):
        """后台线程报错：提示 + 把可能卡住的按钮复位（麦克风是典型）。"""
        self._add_message("error", msg)
        try:
            if self.mic_btn.isChecked():
                self.mic_btn.setChecked(False)
        except Exception:
            pass

    def _on_voice_text(self, text):
        """转写结果回填输入框：空则提示，非空则追加到已有内容后面。"""
        try:
            self.mic_btn.setChecked(False)
        except Exception:
            pass
        if not (text or "").strip():
            self._add_message("sys", "（没听清，请再说一次）")
            return
        cur = self.input.toPlainText().strip()
        self.input.setPlainText((cur + " " + text.strip()).strip())
        self._autosize()
        self.input.setFocus()
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self._add_message("sys", "语音识别完成（%d 字），可编辑后发送" % len(text.strip()))

    # ---------- 图片 OCR（本地 RapidOCR） ----------
    def _ocr_btn_reset(self):
        self.ocr_btn.setEnabled(True)
        self.ocr_btn.setText("提取文字")

    def _ocr_image(self):
        path = self._pending_image
        if not path or self.busy:
            return
        dp = docparse_module()
        if dp is None:
            self._add_message("error", "OCR 组件不可用（打包时未包含 server.docparse）")
            return
        if not path.lower().endswith(tuple(dp.OCR_EXTS)):
            self._add_message("error", "该图片格式不支持 OCR：%s"
                              % os.path.basename(path))
            return
        self.ocr_btn.setEnabled(False)
        self.ocr_btn.setText("识别中…")
        self._add_message("sys", "正在识别图片文字…")
        w = DocWorker(path, ocr=True)
        w.done.connect(self._on_ocr_done)
        w.failed.connect(self._on_ocr_failed)
        self._ocr_worker = w                 # 持有引用，防 GC
        w.start()

    def _on_ocr_done(self, path, text):
        self._ocr_btn_reset()
        if not text.strip():
            self._add_message("sys", "图片中未识别到文字")
            return
        cur = self.input.toPlainText()
        add = ("以下是图片中提取的文字：\n" + text if not cur.strip()
               else "\n\n图片文字：\n" + text)
        self.input.setPlainText(cur + add)
        self._autosize()
        self.input.setFocus()
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self._add_message("sys", "已提取图片文字 %d 字，可编辑后发送" % len(text))

    def _on_ocr_failed(self, path, err):
        self._ocr_btn_reset()
        self._add_message("error", "图片文字提取失败：%s" % err)

    # ---------- 会话持久化 ----------
    def _session_messages(self):
        out = []
        for it in self._bubbles:
            role = getattr(it, "role", None)
            if role in ("ai", "me") and hasattr(it, "whoLbl"):
                out.append({"role": role, "text": it._raw,
                            "who": it.whoLbl.text(), "ts": it.timeLbl.text(),
                            "hist": getattr(it, "in_history", True)})
        return out

    def _save_session(self):
        if self._loading:
            return
        try:
            data = {"model": self.model, "messages": self._session_messages()}
            _store().put(self.sid, data)
        except Exception as e:
            log("save_session failed: %s" % e)

    def _load_session(self):
        path = str(_store().path_for(self.sid))
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log("load_session failed: %s" % e)
            return
        msgs = data.get("messages") or []
        if not msgs:
            return
        self._loading = True
        try:
            self._remove_welcome()
            for m in msgs[-40:]:
                role = m.get("role")
                if role not in ("ai", "me"):
                    continue
                self._add_message(role, m.get("text", ""),
                                  who=m.get("who", ""), ts=m.get("ts", ""))
                if m.get("hist", True):
                    self.history.append({
                        "role": "assistant" if role == "ai" else "user",
                        "content": m.get("text", "")})
            self._trim_history()
        finally:
            self._loading = False
        self._scroll_bottom()

    def _clear_feed(self):
        """清空对话区所有控件（含 sys/error 提示条），welcome 由调用方重建。"""
        # 注意：feed.v 里放的是"阅读列"的布局项，消息在 msg_layout 里
        lay = self.msg_layout
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            if w is self.think_item:
                w.stop()
            w.deleteLater()
        self._bubbles.clear()
        self.think_item = None
        self.ai_item = None
        self._welcome = None

    def _clear_session(self):
        self._cite_sources = []
        if self.busy:
            self._stop()
        self._loading = True
        try:
            self._clear_feed()
            self.history.clear()
            self._clear_image()
            self._clear_doc()
            self._last_image = None
            self._last_doc = None
            self._doc_question = ""
            self._deep_stage = 0
            self._deep_draft = ""
            self._deep_crit = ""
            self._last_failed_text = ""
            self._add_welcome()
        finally:
            self._loading = False
        try:
            # 多会话下"清空"= 清空当前会话内容，而不是把会话删掉
            _store().put(self.sid, {"model": self.model, "messages": []},
                         title="新会话")
        except Exception:
            pass
        self._set_hint_text()
        self._refresh_session_btn()

    # ---------- 启动引导 ----------
    def _on_status(self, state, text):
        self.status.set_state(state, text or None)

    def _on_progress(self, text):
        self.status.set_state("pulling", text)

    def _sys(self, text):
        self._add_message("sys", text)

    def _bootstrap(self):
        # 注意: 本函数运行在普通 threading.Thread(无 Qt 事件循环),
        # 所有 UI 更新必须经 Bridge 信号(QueuedConnection 派发到主线程),
        # 不能用 QTimer.singleShot(定时器在本线程不会触发)。
        def st(state, text=""):
            _emit(self.bridge.status, state, text)
        log("=== bootstrap 开始 (frozen=%s) ===" % getattr(sys, "frozen", False))
        st("checking", "检查服务中…")
        if not ensure_ollama():
            st("offline", "未连接 Ollama")
            _emit(self.bridge.sys, "内置便携 Ollama 启动失败。常见原因：① 杀毒软件/Windows SmartScreen "
                                 "拦截了 _internal/ollama/ollama.exe（请将其加入白名单后重试）；"
                                 "② 11434 端口被占用。详细原因见同目录 offline_chat_debug.log / "
                                 "offline_chat_ollama_serve.log，可发回排查。")
            return
        # 打包态且带内置模型清单: 首次运行把内置 GGUF 导入本地模型库(离线, 无需联网)
        bundled = os.path.isfile(os.path.join(_resource_dir(), "bundle_manifest.json"))
        if getattr(sys, "frozen", False) and bundled:
            st("pulling", "首次导入内置模型…")
            _emit(self.bridge.sys, "首次运行正在导入内置离线模型（约十几 GB），请耐心等待，期间请勿关闭窗口…")
        def _prog(done, total, model, status):
            _emit(self.bridge.progress, "导入模型 %d/%d · %s" % (done, total, model))
        if bundled:
            _install_bundled_models(progress_cb=_prog)
        if not has_model(self.model):
            if getattr(sys, "frozen", False) and bundled:
                st("error", "模型缺失")
                failed = _bundled_install_failures
                tip = ("内置模型导入失败（缺失：%s）。请确认：① 系统盘 C 盘有约 15GB 空闲；"
                       "② 解压完整（GGUF 文件未被杀软删除/损坏，建议用 7-Zip/WinRAR 解压而非"
                       "资源管理器）；③ 解压路径尽量为纯英文短路径。详情见同目录 "
                       "offline_chat_debug.log。" % (", ".join(failed) if failed else self.model))
                _emit(self.bridge.sys, tip)
                return
            st("pulling", "下载模型 %s…" % self.model)
            _emit(self.bridge.sys, "首次使用需联网下载模型 %s，请稍候…" % self.model)
            try:
                pull_model(self.model)
                st("online", "Ollama 在线")
                _emit(self.bridge.sys, "模型下载完成，可以开始聊天")
            except Exception as e:
                st("error", "下载失败")
                _emit(self.bridge.sys, "模型下载失败：%s" % e)
                return
        else:
            st("online", "Ollama 在线")
        self._load_models()

    def _load_models(self):
        try:
            with _OPENER.open(OLLAMA_BASE + "/api/tags", timeout=5) as r:
                d = json.loads(r.read().decode())
            names = [m["name"] for m in d.get("models", [])]
            _emit(self.bridge.models, names)
        except Exception:
            pass

    def _apply_models(self, names):
        if not names:
            return
        self.model_menu.clear()
        self._model_actions = []
        head = self.model_menu.addAction("本地模型 · 切换会自动释放上一个")
        head.setEnabled(False)
        self.model_menu.addSeparator()
        for n in names:
            act = self.model_menu.addAction(n)   # addAction(text) 返回菜单父拥有的 action
            act.setCheckable(True)
            act.setChecked(n == self.model)
            if n == self.model:
                act.setIcon(make_icon("check", _icon_color("moss", "#1F5C4A"), 13))
            act.triggered.connect(lambda _=False, x=n: self._select_model(x))
            self._model_actions.append(act)       # 保存 Python 引用, 防被垃圾回收
        self.model_menu.addSeparator()
        tip = self.model_menu.addAction("8B 及以上在 16G 内存机器上较慢")
        tip.setEnabled(False)
        if self.model not in names:
            self._select_model(names[0])

    def _model_sizes(self):
        """向本机 Ollama 查每个模型的体积（失败就返回空，浮层只显示名字）。"""
        out = {}
        try:
            with _OPENER.open(OLLAMA_BASE + "/api/tags", timeout=0.8) as r:
                data = json.load(r)
            for m in data.get("models") or []:
                nm = m.get("name") or ""
                gb = (m.get("size") or 0) / 1e9
                if nm and gb:
                    out[nm] = "%.1fG" % gb
        except Exception:
            pass
        return out

    def _show_model_popup(self):
        names = [a.text() for a in getattr(self, "_model_actions", [])]
        if not names:
            names = [self.model]
        pop = ModelPopup(self.window(), names, self._model_sizes(), self.model,
                         self._select_model)
        global_pos = self.model_btn.mapToGlobal(
            self.model_btn.rect().bottomLeft())
        pop.move(global_pos.x() - (pop.width() - self.model_btn.width()), global_pos.y() + 6)
        pop.show()

    def _select_model(self, name):
        old = getattr(self, "model", "")
        self.model = name
        self.model_btn.setText(name)
        # 菜单里的勾选/图标跟着走（原生菜单不会自动换图标）
        for act in getattr(self, "_model_actions", []):
            on = act.text() == name
            act.setChecked(on)
            act.setIcon(make_icon("check", _icon_color("moss", "#1F5C4A"), 13) if on
                        else QIcon())
        self._set_hint_text()
        try:
            self._settings.setValue("model", name)
        except Exception:
            pass
        # 关键：本机内存有限（16GB，两个 4B/8B 同时驻留就吃满），
        # 切模型后必须把其它模型卸载，否则推理会退化成 "0000" 这类垃圾输出
        if old and old != name:
            self.unload_models_except(name)
            self._sys("已切换模型：%s（已释放上一个模型占用的内存）" % name)

    def unload_models_except(self, keep: str):
        """后台卸载除 keep 以外的所有已加载模型（不阻塞 UI）。"""
        import threading
        threading.Thread(target=self._unload_bg, args=(keep,), daemon=True).start()

    def _unload_bg(self, keep: str):
        try:
            with _OPENER.open(OLLAMA_BASE + "/api/ps", timeout=3) as r:
                data = json.load(r)
        except Exception:
            return
        for m in data.get("models") or []:
            name = (m.get("name") or "").strip()
            if not name or name == keep:
                continue
            try:
                body = json.dumps({"model": name, "prompt": "",
                                   "keep_alive": 0}).encode()
                req = urllib.request.Request(
                    OLLAMA_BASE + "/api/generate", data=body,
                    headers={"Content-Type": "application/json"})
                with _OPENER.open(req, timeout=15) as resp:
                    resp.read()
                log("unloaded model: %s" % name)
            except Exception as e:
                log("unload %s failed: %s" % (name, e))

    def closeEvent(self, e):
        try:
            self._settings.setValue("geometry", self.saveGeometry())
            self._settings.setValue("model", self.model)
        except Exception:
            pass
        self._save_session()
        super().closeEvent(e)

    # ---------- 事件 ----------
    def eventFilter(self, obj, event):
        if obj is self.input:
            t = event.type()
            if t == QEvent.KeyPress:
                if event.matches(QKeySequence.Paste):
                    img = QGuiApplication.clipboard().image()
                    if not img.isNull():
                        try:
                            path = os.path.join(_data_dir(), "clipboard_%d.png"
                                                % int(time.time() * 1000))
                            img.save(path, "PNG")
                            self._attach_image(path)
                            return True
                        except Exception:
                            pass
                if event.key() == Qt.Key_Escape:
                    self._stop_if_busy()
                    return True
                if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
                    self._send()
                    return True
            elif t == QEvent.FocusIn:
                self.composer_card.setStyleSheet(
                    "#composerCard{background:#FFFFFF;border:1px solid #3B5BDB;border-radius:16px;}")
            elif t == QEvent.FocusOut:
                self.composer_card.setStyleSheet(
                    "#composerCard{background:#FFFFFF;border:1px solid #E6E8F0;border-radius:16px;}")
        return super().eventFilter(obj, event)


def _app_icon_path():
    """app.ico 路径: 打包后取内置资源目录, 开发态取脚本同目录。"""
    if getattr(sys, "frozen", False):
        base = _resource_dir() or os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    # 兼容两种打包布局: ./app.ico 与 ./app.ico/app.ico(旧 datas 写法)
    for p in (os.path.join(base, "app.ico"),
              os.path.join(base, "app.ico", "app.ico")):
        if os.path.isfile(p):
            return p
    return os.path.join(base, "app.ico")


_GLOBAL_SHARED = None


def main():
    global _GLOBAL_SHARED
    # 显式声明 AppUserModelID, 否则 Windows 任务栏可能回退显示默认 Python 图标
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("offline.chat.1")
    except Exception:
        pass
    app = QApplication(sys.argv)
    # 单实例：避免多个进程同时启动 serve / 重复导入模型
    _GLOBAL_SHARED = QSharedMemory("offline_chat_single_instance")
    if not _GLOBAL_SHARED.create(1):
        QMessageBox.information(None, "离线AI聊天", "程序已经在运行中。")
        sys.exit(0)
    icon = QIcon(_app_icon_path())
    if not icon.isNull():
        app.setWindowIcon(icon)  # 标题栏 + 任务栏统一用 app.ico
    w = ChatWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
