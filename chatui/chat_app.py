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
import shutil
import subprocess
import threading
import urllib.request
import urllib.error
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QTextEdit, QPushButton, QLabel, QFrame, QScrollArea, QMenu,
                               QSizePolicy, QGraphicsOpacityEffect)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QEvent, QRectF, QObject, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (QPixmap, QPainter, QPainterPath, QPen, QColor, QIcon, QAction,
                           QGuiApplication, QBrush, QLinearGradient)

OLLAMA_BASE = "http://127.0.0.1:11434"  # 用 IPv4 直连, 避免 localhost->::1 回退延迟

# Ollama 是本地服务, 强制不走 HTTP 代理(避免企业代理/沙箱代理劫截 localhost 请求)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
        return "ollama"
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
    marker = os.path.join(dst, ".copied")
    if os.path.exists(marker):
        return True
    try:
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        open(marker, "w").close()
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
    if not os.path.exists(exe):
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


def _install_bundled_models():
    """首次运行时把内置 GGUF 通过 `ollama create` 导入本地模型库(完全离线)。
    仅打包态(frozen)生效; 已存在的模型会跳过, 避免重复导入与重复占用磁盘。
    全程写入离线_chat_debug.log; 任一模型导入失败都会记录, 不会静默吞错。"""
    global _bundled_install_failures
    _bundled_install_failures = []
    if not getattr(sys, "frozen", False):
        return
    base = _resource_dir()
    log("_install_bundled_models: resource_dir=%s" % base)
    if not base:
        log("_install_bundled_models: 资源目录定位失败")
        return
    manifest_path = os.path.join(base, "bundle_manifest.json")
    if not os.path.isfile(manifest_path):
        log("_install_bundled_models: 找不到 %s" % manifest_path)
        return
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        log("_install_bundled_models: 读取清单失败: %s" % e)
        return
    exe = _ollama_exe()
    if not os.path.exists(exe):
        log("_install_bundled_models: ollama.exe 不存在")
        return
    ok_count = 0
    for m in manifest:
        model = m.get("model")
        modelfile = os.path.join(base, "bundle_models", m.get("safe", ""), "Modelfile")
        if not model or not os.path.isfile(modelfile):
            # Modelfile 缺失 = 打包缺陷(历史版本曾漏打), 必须计为失败,
            # 否则 0/9 全跳过也会被当成"无失败", 误导用户且不重试。
            log("  [FAIL] 缺 Modelfile: %s (应为 bundle_models/%s/Modelfile)"
                % (model or "?", m.get("safe", "?")))
            if model:
                _bundled_install_failures.append(model)
            continue
        try:
            if has_model(model):   # 已导入则跳过
                log("  已存在, 跳过: %s" % model)
                ok_count += 1
                continue
        except Exception as e:
            log("  has_model 检查异常(%s): %s" % (model, e))
        gguf = os.path.join(os.path.dirname(modelfile), m.get("gguf_name", ""))
        if not os.path.isfile(gguf) or os.path.getsize(gguf) < 1024 * 1024:
            log("  [FAIL] GGUF 缺失或损坏(<1MB): %s" % gguf)
            _bundled_install_failures.append(model)
            continue
        log("  开始导入: %s (from %s)" % (model, gguf))
        try:
            r = subprocess.run([exe, "create", model, "-f", modelfile],
                               cwd=os.path.dirname(modelfile),
                               timeout=1800, check=False,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out = (r.stdout or b"").decode(errors="replace")
            err = (r.stderr or b"").decode(errors="replace")
            if r.returncode == 0 and has_model(model):
                log("  [OK] %s (%.1fs)" % (model, 0))
                ok_count += 1
            else:
                log("  [FAIL] %s rc=%s\n    stdout=%s\n    stderr=%s"
                    % (model, r.returncode, out[-400:], err[-400:]))
                _bundled_install_failures.append(model)
        except Exception as e:
            log("  [FAIL] %s 异常: %s" % (model, e))
            _bundled_install_failures.append(model)
    log("_install_bundled_models: 成功 %d / 共 %d, 失败=%s"
        % (ok_count, len(manifest), _bundled_install_failures))
    # 真正全部成功(9/9 且无失败)才写标记; 跳过/缺失也计入失败口径, 避免误标"已完成"
    if ok_count == len(manifest) and not _bundled_install_failures:
        try:
            os.makedirs(_persistent_ollama_dir(), exist_ok=True)
            open(os.path.join(_persistent_ollama_dir(), ".bundled_models_v1"), "w").close()
        except Exception:
            pass


# ==================== 推理线程 ====================
class Worker(QThread):
    token = Signal(str)
    finished = Signal()
    errored = Signal(str)

    def __init__(self, model, prompt):
        super().__init__()
        self.model = model
        self.prompt = prompt

    def run(self):
        try:
            payload = json.dumps(
                {"model": self.model,
                 "messages": [{"role": "user", "content": self.prompt}],
                 "stream": True}
            ).encode()
            req = urllib.request.Request(
                OLLAMA_BASE + "/api/chat", data=payload,
                headers={"Content-Type": "application/json"})
            with _OPENER.open(req, timeout=120) as resp:
                buf = ""
                for line in resp:
                    line = line.decode().strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    tok = obj.get("message", {}).get("content", "")
                    if tok:
                        buf += tok
                        self.token.emit(buf)
                    if obj.get("done"):
                        break
        except urllib.error.URLError as e:
            self.errored.emit("连接 Ollama 失败：%s  请确认 Ollama 已启动" % e)
        except Exception as e:
            self.errored.emit("错误：%s" % e)
        finally:
            self.finished.emit()


# ==================== 跨线程信号桥 (worker 线程 -> 主线程) ====================
class Bridge(QObject):
    status = Signal(str, str)   # state, text
    sys = Signal(str)           # 系统提示
    models = Signal(list)       # 模型名列表


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


def make_icon(kind, color, size):
    S = 24.0
    dpr = 2
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / S, size / S)
    col = QColor(color)
    if kind in ("bolt", "send"):
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
    p.end()
    return QIcon(pm)


def _badge_pixmap(size, gradient, glyph_draw):
    dpr = 2
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
        s = self.STATES.get(state, self.STATES["checking"])
        if text is not None:
            self.txt.setText(text)
        self.setStyleSheet(
            "#statusPill{background:%s;border:1px solid %s;border-radius:14px;}"
            "#statusPill QLabel{color:%s;font-size:12px;background:transparent;border:none;}"
            % (s["bg"], s["bd"], s["tx"]))
        self.dot.setStyleSheet("background:%s;border-radius:4px;" % s["dot"])


# ==================== 消息气泡 ====================
class BubbleItem(QWidget):
    def __init__(self, role, text="", who="", ts="", parent=None):
        super().__init__(parent)
        self.role = role
        self._raw = text
        if role in ("sys", "error"):
            self.chip = QFrame()
            self.chip.setObjectName("errChip" if role == "error" else "sysChip")
            cl = QHBoxLayout(self.chip)
            cl.setContentsMargins(13, 5, 13, 5)
            self.label = QLabel(_rich(text))
            self.label.setWordWrap(True)
            self.label.setTextFormat(Qt.RichText)
            cl.addWidget(self.label)
            outer = QHBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addStretch(1)
            outer.addWidget(self.chip)
            outer.addStretch(1)
            return

        is_me = role == "me"
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        self.avatar = QLabel()
        self.avatar.setFixedSize(30, 30)
        self.avatar.setPixmap(make_user_avatar(30) if is_me else make_ai_avatar(30))

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        meta = QHBoxLayout()
        meta.setContentsMargins(2, 0, 2, 0)
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
        self.label = QLabel(_rich(text))
        self.label.setObjectName("bubbleTxt")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.RichText)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(13, 10, 13, 11)
        bl.addWidget(self.label)
        col.addWidget(self.bubble)

        if is_me:
            outer.addLayout(col)
            outer.addWidget(self.avatar)
        else:
            outer.addWidget(self.avatar)
            outer.addLayout(col)
            outer.addStretch(1)

        self.copyBtn = QPushButton(self.bubble)
        self.copyBtn.setObjectName("copyBtn")
        self.copyBtn.setIcon(make_icon("copy", "#5A6172", 13))
        self.copyBtn.setIconSize(QSize(13, 13))
        self.copyBtn.setText("复制")
        self.copyBtn.setCursor(Qt.PointingHandCursor)
        self.copyBtn.setFocusPolicy(Qt.NoFocus)
        self.copyBtn.clicked.connect(self._copy)
        self.copyBtn.hide()

    def set_text(self, text):
        self._raw = text
        self.label.setText(_rich(text))

    def set_max_width(self, px):
        if self.role in ("ai", "me"):
            self.bubble.setMaximumWidth(px)

    def _copy(self):
        QGuiApplication.clipboard().setText(self._raw)
        self.copyBtn.setIcon(make_icon("check", "#15803D", 13))
        self.copyBtn.setText("已复制")
        QTimer.singleShot(1400, self._reset_copy)

    def _reset_copy(self):
        self.copyBtn.setIcon(make_icon("copy", "#5A6172", 13))
        self.copyBtn.setText("复制")

    def _place_copy(self):
        if hasattr(self, "copyBtn"):
            self.copyBtn.adjustSize()
            self.copyBtn.move(self.bubble.width() - self.copyBtn.width() - 6, 6)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "copyBtn") and self.copyBtn.isVisible():
            self._place_copy()

    def enterEvent(self, e):
        super().enterEvent(e)
        if hasattr(self, "copyBtn"):
            self._place_copy()
            self.copyBtn.show()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if hasattr(self, "copyBtn"):
            self.copyBtn.hide()


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


# ==================== 主窗口 ====================
class ChatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("离线AI聊天 · 本地 Ollama")
        self.resize(980, 740)
        self.setMinimumSize(560, 480)
        self.setStyleSheet(build_qss())
        self.model = "qwen3:4b"
        self.busy = False
        self.ai_item = None
        self.think_item = None
        self._bubbles = []
        self._welcome = None
        self._model_actions = []
        self.bridge = Bridge()
        self.bridge.status.connect(self._on_status)
        self.bridge.sys.connect(self._sys)
        self.bridge.models.connect(self._apply_models)
        self._build()
        threading.Thread(target=self._bootstrap, daemon=True).start()

    # ---------- 布局 ----------
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        hdr = QFrame()
        hdr.setObjectName("hdr")
        hdr.setFixedHeight(60)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 14, 0)
        hl.setSpacing(12)
        logo = QLabel()
        logo.setFixedSize(30, 30)
        logo.setPixmap(make_logo(30))
        hl.addWidget(logo)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        name = QLabel("离线AI聊天")
        name.setObjectName("appName")
        sub = QLabel("本地推理 · 断网可用 · 数据不外传")
        sub.setObjectName("appSub")
        titles.addWidget(name)
        titles.addWidget(sub)
        hl.addLayout(titles)
        hl.addStretch(1)
        self.status = StatusPill()
        hl.addWidget(self.status)
        self.model_btn = QPushButton(self.model)
        self.model_btn.setObjectName("modelBtn")
        self.model_btn.setCursor(Qt.PointingHandCursor)
        self.model_menu = QMenu(self)
        self.model_btn.setMenu(self.model_menu)
        hl.addWidget(self.model_btn)
        root.addWidget(hdr)

        # 对话滚动区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.feed = FeedWidget(self._recap)
        self.scroll.setWidget(self.feed)
        root.addWidget(self.scroll, 1)
        self._add_welcome()

        # 输入区
        card = QFrame()
        card.setObjectName("composerCard")
        self.composer_card = card
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 9, 12, 8)
        cl.setSpacing(4)
        self.input = QTextEdit()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("输入消息…")
        self.input.setMinimumHeight(46)
        self.input.setMaximumHeight(120)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.input.setAcceptRichText(False)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._on_input_changed)
        cl.addWidget(self.input)
        cbar = QHBoxLayout()
        cbar.setContentsMargins(2, 0, 0, 0)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self._set_hint_text()
        cbar.addWidget(self.hint)
        cbar.addStretch(1)
        self.send_btn = QPushButton()
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(38, 38)
        self.send_btn.setIcon(make_icon("send", "#FFFFFF", 18))
        self.send_btn.setIconSize(QSize(18, 18))
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("发送")
        self.send_btn.clicked.connect(self._send)
        self.send_btn.setEnabled(False)
        cbar.addWidget(self.send_btn)
        cl.addLayout(cbar)

        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(16, 10, 16, 14)
        wl.addWidget(card)
        root.addWidget(wrap)

    # ---------- 欢迎卡 ----------
    def _add_welcome(self):
        if self._welcome is not None:
            return
        w = QFrame()
        w.setObjectName("welcome")
        w.setMaximumWidth(540)
        l = QVBoxLayout(w)
        l.setContentsMargins(28, 30, 28, 28)
        l.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(make_logo(52))
        logo.setAlignment(Qt.AlignCenter)
        l.addWidget(logo, 0, Qt.AlignCenter)
        t = QLabel("你好，我是本地 AI")
        t.setObjectName("wTitle")
        t.setAlignment(Qt.AlignCenter)
        l.addWidget(t)
        s = QLabel("完全本地推理 · 断网可用 · 不上传任何数据")
        s.setObjectName("wSub")
        s.setAlignment(Qt.AlignCenter)
        l.addWidget(s)
        st = QLabel("● Ollama 在线 · %s · CPU" % self.model)
        st.setObjectName("wStatus")
        st.setAlignment(Qt.AlignCenter)
        l.addWidget(st)
        l.addSpacing(6)
        cl = QHBoxLayout()
        cl.setSpacing(8)
        for c in ["帮我写一封邮件", "解释一个概念", "总结这段文字", "头脑风暴"]:
            b = QPushButton(c)
            b.setObjectName("chip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, x=c: self._use_suggestion(x))
            cl.addWidget(b)
        l.addLayout(cl)
        self.feed.v.addWidget(w, 0, Qt.AlignHCenter)
        self._welcome = w

    def _remove_welcome(self):
        if self._welcome is not None:
            self.feed.v.removeWidget(self._welcome)
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
        if role in ("sys", "error"):
            return Qt.AlignHCenter
        return Qt.AlignLeft

    def _add_message(self, role, text, who="", ts=""):
        item = BubbleItem(role, text, who, ts)
        self.feed.v.addWidget(item, 0, self._align_for(role))
        if role in ("ai", "me"):
            self._bubbles.append(item)
        self._recap()
        self._scroll_bottom()
        return item

    def _add_thinking(self):
        item = ThinkingItem()
        self.feed.v.addWidget(item, 0, Qt.AlignLeft)
        self._bubbles.append(item)
        self._recap()
        self._scroll_bottom()
        return item

    def _remove_item(self, item):
        if item in self._bubbles:
            self._bubbles.remove(item)
        self.feed.v.removeWidget(item)
        if item is self.think_item:
            item.stop()
        item.deleteLater()

    def _recap(self):
        w = self.feed.width()
        if w <= 0:
            return
        mw = int(w * 0.74)
        for it in self._bubbles:
            it.set_max_width(mw)

    def _scroll_bottom(self):
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar()
                          .setValue(self.scroll.verticalScrollBar().maximum()))

    @staticmethod
    def _now():
        import datetime
        return datetime.datetime.now().strftime("%H:%M")

    # ---------- 发送 / 流式 ----------
    def _set_hint_text(self):
        self.hint.setText('Enter 发送 · Shift+Enter 换行　·　'
                          '<span style="color:#5A6172;">CPU · %s</span>' % self.model)

    def _on_input_changed(self):
        self._autosize()
        self.send_btn.setEnabled((not self.busy) and bool(self.input.toPlainText().strip()))

    def _autosize(self):
        h = self.input.document().size().height()
        self.input.setFixedHeight(max(46, min(120, int(h) + 12)))

    def _send(self):
        if self.busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self._remove_welcome()
        self.input.clear()
        self._autosize()
        self._add_message("me", text, who="你", ts=self._now())
        self.busy = True
        self.send_btn.setEnabled(False)
        self.think_item = self._add_thinking()
        self.worker = Worker(self.model, text)
        self.worker.token.connect(self._on_token)
        self.worker.finished.connect(self._finish)
        self.worker.errored.connect(self._on_error)
        self.worker.start()

    def _on_token(self, buf):
        if self.think_item is not None:
            self._remove_item(self.think_item)
            self.think_item = None
        if self.ai_item is None:
            self.ai_item = self._add_message("ai", buf, who="AI", ts=self._now())
        else:
            self.ai_item.set_text(buf)
            self._scroll_bottom()

    def _finish(self):
        self.think_item = None
        self.ai_item = None
        self.busy = False
        self.send_btn.setEnabled(bool(self.input.toPlainText().strip()))
        self._scroll_bottom()

    def _on_error(self, msg):
        if self.think_item is not None:
            self._remove_item(self.think_item)
            self.think_item = None
        self.ai_item = None
        self._add_message("error", msg)
        self.busy = False
        self.send_btn.setEnabled(bool(self.input.toPlainText().strip()))

    # ---------- 启动引导 ----------
    def _on_status(self, state, text):
        self.status.set_state(state, text or None)

    def _sys(self, text):
        self._add_message("sys", text)

    def _bootstrap(self):
        # 注意: 本函数运行在普通 threading.Thread(无 Qt 事件循环),
        # 所有 UI 更新必须经 Bridge 信号(QueuedConnection 派发到主线程),
        # 不能用 QTimer.singleShot(定时器在本线程不会触发)。
        def st(state, text=""):
            self.bridge.status.emit(state, text)
        log("=== bootstrap 开始 (frozen=%s) ===" % getattr(sys, "frozen", False))
        st("checking", "检查服务中…")
        if not ensure_ollama():
            st("offline", "未连接 Ollama")
            self.bridge.sys.emit("内置便携 Ollama 启动失败。常见原因：① 杀毒软件/Windows SmartScreen "
                                 "拦截了 _internal/ollama/ollama.exe（请将其加入白名单后重试）；"
                                 "② 11434 端口被占用。详细原因见同目录 offline_chat_debug.log / "
                                 "offline_chat_ollama_serve.log，可发回排查。")
            return
        # 打包态: 首次运行把内置 GGUF 导入本地模型库(离线, 无需联网)
        if getattr(sys, "frozen", False):
            st("pulling", "首次导入内置模型…")
            self.bridge.sys.emit("首次运行正在导入内置离线模型（约十几 GB），请耐心等待，期间请勿关闭窗口…")
        _install_bundled_models()
        if not has_model(self.model):
            if getattr(sys, "frozen", False):
                st("error", "模型缺失")
                failed = _bundled_install_failures
                tip = ("内置模型导入失败（缺失：%s）。请确认：① 系统盘 C 盘有约 15GB 空闲；"
                       "② 解压完整（GGUF 文件未被杀软删除/损坏，建议用 7-Zip/WinRAR 解压而非"
                       "资源管理器）；③ 解压路径尽量为纯英文短路径。详情见同目录 "
                       "offline_chat_debug.log。" % (", ".join(failed) if failed else self.model))
                self.bridge.sys.emit(tip)
                return
            st("pulling", "下载模型 %s…" % self.model)
            self.bridge.sys.emit("首次使用需联网下载模型 %s，请稍候…" % self.model)
            try:
                pull_model(self.model)
                st("online", "Ollama 在线")
                self.bridge.sys.emit("模型下载完成，可以开始聊天")
            except Exception as e:
                st("error", "下载失败")
                self.bridge.sys.emit("模型下载失败：%s" % e)
                return
        else:
            st("online", "Ollama 在线")
        self._load_models()

    def _load_models(self):
        try:
            with _OPENER.open(OLLAMA_BASE + "/api/tags", timeout=5) as r:
                d = json.loads(r.read().decode())
            names = [m["name"] for m in d.get("models", [])]
            self.bridge.models.emit(names)
        except Exception:
            pass

    def _apply_models(self, names):
        if not names:
            return
        self.model_menu.clear()
        self._model_actions = []
        for n in names:
            act = self.model_menu.addAction(n)   # addAction(text) 返回菜单父拥有的 action
            act.setCheckable(True)
            act.setChecked(n == self.model)
            act.triggered.connect(lambda _=False, x=n: self._select_model(x))
            self._model_actions.append(act)       # 保存 Python 引用, 防被垃圾回收
        if self.model not in names:
            self._select_model(names[0])

    def _select_model(self, name):
        self.model = name
        self.model_btn.setText(name)
        self._set_hint_text()

    # ---------- 事件 ----------
    def eventFilter(self, obj, event):
        if obj is self.input:
            t = event.type()
            if t == QEvent.KeyPress:
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


def main():
    # 显式声明 AppUserModelID, 否则 Windows 任务栏可能回退显示默认 Python 图标
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("offline.chat.1")
    except Exception:
        pass
    app = QApplication(sys.argv)
    icon = QIcon(_app_icon_path())
    if not icon.isNull():
        app.setWindowIcon(icon)  # 标题栏 + 任务栏统一用 app.ico
    w = ChatWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
