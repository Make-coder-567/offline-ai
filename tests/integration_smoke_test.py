"""集成连跑：把主要交互串一遍，只验证"不抛异常 + 状态正确"。"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, r"E:\Workbuddy\离线AI\offline-ai")
sys.path.insert(0, r"E:\Workbuddy\离线AI\offline-ai\chatui")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
import chat_app as ca  # noqa: E402
from sessions import SessionStore  # noqa: E402

tmp = tempfile.mkdtemp()
STORE = SessionStore(tmp)
ca._store = lambda: STORE

fails = []


def step(name, fn):
    try:
        fn()
        print("[OK]  ", name)
    except Exception as e:
        fails.append(name)
        import traceback
        traceback.print_exc()
        print("[FAIL]", name, e)


class FakeWorker(ca.Worker):
    last = None

    def __init__(self, model, messages, temperature=0.3, num_ctx=6144):
        super().__init__(model, messages, temperature, num_ctx)
        FakeWorker.last = (model, messages)
        self.result_text = ""

    def start(self):
        pass

    def cancel(self):
        pass


w = ca.ChatWindow()
w.resize(1100, 740)
w.show()
for _ in range(3):
    app.processEvents()

orig_worker = ca.Worker
ca.Worker = FakeWorker
try:
    step("新建会话", lambda: w._new_session())
    step("发送一条", lambda: (w.input.setPlainText("测试一句话"), w._send()))
    step("流式 token", lambda: (setattr(w.worker, "result_text", "回答内容"),
                             w._on_token("回答内容")))
    step("定稿", lambda: w._finish())

    def pin_then_unpin():
        w._toggle_pin(w.sid)
        w._toggle_pin(w.sid)

    step("置顶/取消置顶", pin_then_unpin)
    step("重命名", lambda: (STORE.rename(w.sid, "改了名字"), w._refresh_sessions()))

    def theme_roundtrip():
        w.theme_action.setChecked(True)
        w._toggle_theme(True)
        w._toggle_theme(False)

    step("主题切换往返", theme_roundtrip)
    step("模型浮层构建", lambda: w._show_model_popup())
    step("引用点击（无来源）", lambda: w._show_cite("1"))

    def session_cycle():
        sid2 = STORE.create("另一个会话")
        w._switch_session(sid2)
        w._switch_session(w.sid)

    step("会话切换往返", session_cycle)
    step("清空会话", lambda: w._clear_session())
    step("删除多余会话", lambda: [STORE.delete(r["id"]) for r in STORE.all()[1:]])
    step("刷新列表", lambda: w._refresh_sessions())
finally:
    ca.Worker = orig_worker

print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)
