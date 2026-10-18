"""无头渲染真实界面，用于 UI 迭代自查（不弹窗、不写真实会话）。

用法：python design/preview_app.py
产物：design/ui-batch3-{chat,empty,night,modelpop}.png
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, r"E:\Workbuddy\离线AI\offline-ai")
sys.path.insert(0, r"E:\Workbuddy\离线AI\offline-ai\chatui")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
import chat_app as ca  # noqa: E402

DEMO = [
    {"id": "s1", "title": "切换模型后输出 0000", "count": 6, "pinned": True,
     "updated": "2026-10-18 15:02:00"},
    {"id": "s2", "title": "传感器标定报告要点", "count": 4,
     "updated": "2026-10-18 14:02:00"},
    {"id": "s3", "title": "把这段 Python 改成向量化", "count": 9,
     "updated": "2026-10-18 11:36:00"},
    {"id": "s4", "title": "教师节方案初稿", "count": 12,
     "updated": "2026-10-17 21:14:00"},
    {"id": "s5", "title": "GOSPA 指标怎么解释", "count": 3,
     "updated": "2026-10-17 16:50:00"},
]


class FakeStore:
    def all(self):
        return DEMO

    def meta(self, sid):
        for r in DEMO:
            if r["id"] == sid:
                return r
        return DEMO[0]

    def migrate_legacy(self, p):
        return False

    def ensure(self, sid):
        return sid

    def path_for(self, sid):
        return os.path.join(os.environ.get("TEMP", "."), "preview_no_session.json")

    def get(self, sid):
        return {}

    def put(self, *a, **k):
        pass

    def set_pinned(self, sid, pinned):
        for r in DEMO:
            if r["id"] == sid:
                r["pinned"] = pinned
        return True


ca._store = lambda: FakeStore()

w = ca.ChatWindow()
w.sid = "s1"
w.resize(1080, 720)
w.show()
for _ in range(3):
    app.processEvents()
w.status.set_state("online", "Ollama 在线")

OUT = r"E:\Workbuddy\离线AI\offline-ai\design"


def shot(name, bottom=False):
    for _ in range(4):
        app.processEvents()
    try:
        w.feed.v.activate()
    except Exception:
        pass
    if bottom:
        sb = w.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        app.processEvents()
    print(name, w.grab().save(os.path.join(OUT, name)))


# 1) 空状态
w._apply_theme()
shot("ui-batch3-empty.png")

# 2) 会话（含引用与置顶）
w._remove_welcome()
app.processEvents()
w._add_message("me", "这份标定报告里，传感器采样率是多少？", who="", ts="15:02")
w._cite_sources = [
    {"path": "传感器标定.pdf", "page": "p.3",
     "text": "采样率设定为 20 Hz，量程 0.2–8 m，测距分辨率 1 cm，视场角 90°。"},
    {"path": "传感器标定.pdf", "page": "p.5",
     "text": "标定结论：俯仰角偏差 0.3°，需在装配时补偿；温度漂移 0.02°/℃。"},
]
ai = w._add_message(
    "ai",
    "报告里给出的采样率是 **20 Hz**，量程 0.2–8 m，测距分辨率 1 cm [1]。"
    "另外俯仰角存在 0.3° 偏差、温度漂移 0.02°/℃，装配时需要补偿 [2]。",
    who="gemma3:4b", ts="2.1s · 48 tok")
w._add_message("sys", "已解析《传感器标定.pdf》：8620 字，命中 3 个相关片段")
w._add_message("me", "那 8B 模型跑得动吗？", who="", ts="15:04")
w._add_message("ai", "16G 内存下 8B 能跑但慢，且不要和其它模型同时驻留 —— 切换时会自动释放。",
               who="gemma3:4b", ts="1.4s")
w.input.setPlainText("把这份 PDF 的结论整理成三点")
ai.copyBtn.show()
if getattr(ai, "speakBtn", None) is not None:
    ai.speakBtn.show()
shot("ui-batch3-chat.png", bottom=True)

# 3) 夜读
w.theme = "night"
w._apply_theme()
shot("ui-batch3-night.png", bottom=True)

# 4) 模型浮层（独立顶层窗口，单独截）
w.theme = "paper"
w._apply_theme()
pop = ca.ModelPopup(w, ["gemma3:4b", "gemma3:4b-vision", "qwen3:4b",
                        "qwen2.5-coder:3b", "qwen3:8b"],
                    {"gemma3:4b": "2.5G", "gemma3:4b-vision": "3.3G",
                     "qwen3:4b": "2.5G", "qwen2.5-coder:3b": "2.1G",
                     "qwen3:8b": "5.0G"},
                    "gemma3:4b", lambda n: None)
pop.show()
for _ in range(3):
    app.processEvents()
print("ui-batch3-modelpop.png",
      pop.grab().save(os.path.join(OUT, "ui-batch3-modelpop.png")))
