"""主题令牌与 QSS 生成（墨绿纸感 / 夜读）。

设计来源：design/设计规范_v3_墨绿纸感.md
要点：
  · QSS 不支持 CSS 变量与 color-mix，所以颜色在这里预计算，用 {占位符} 注入
  · 两套主题共享同一份模板，只换令牌，切主题 = 重新 setStyleSheet
  · 字体按可用性挑选，中文字重不依赖系统装了哪套
"""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase

# --------------------------------------------------------------------------
# 令牌
# --------------------------------------------------------------------------
PAPER = dict(
    paper="#F6F4EF", paper2="#EFECE4", surface="#FFFDF9",
    ink="#17211D", ink2="#38443E", muted="#5F6B64", muted2="#8A938C",
    line="#E2DED4", line2="#D3CEC1",
    moss="#1F5C4A", moss_hover="#17493B", moss_tint="#E7F0EA",
    seal="#A63A2E", seal_tint="#FAEEEB",
    amber="#8A5B12", amber_tint="#F7EEDC",
    hover="#F9F7F2", top_bg="#F7F5F0", search_bg="#F2EFE8",
    user_line="#CFE0D6", warn_line="#E4CFA8", err_line="#E7C4BE",
    send_fg="#FFFFFF",
    sel_bg="#FFFDF9",
)
NIGHT = dict(
    paper="#191D1A", paper2="#202521", surface="#232925",
    ink="#ECEFE8", ink2="#C9D1C9", muted="#98A39A", muted2="#78837A",
    line="#313833", line2="#3C443E",
    moss="#7FBFA4", moss_hover="#93CEB5", moss_tint="#1F2E27",
    seal="#E08579", seal_tint="#2E2321",
    amber="#D6A85F", amber_tint="#2C2720",
    hover="#262C27", top_bg="#1B211D", search_bg="#1E231F",
    user_line="#2E4438", warn_line="#4A3D28", err_line="#4A302C",
    send_fg="#16211C",
    sel_bg="#232925",
)
THEMES = {"paper": PAPER, "night": NIGHT}


def pick_font(candidates) -> str:
    """返回系统里第一个可用的字体名（无 QGuiApplication 时直接取首选）。"""
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:
        # QFontDatabase 在无 GUI 应用时会 qFatal 直接终止进程，必须先判空
        return candidates[0]
    try:
        families = set(QFontDatabase.families())
    except Exception:
        return candidates[0]
    for c in candidates:
        if c in families:
            return c
    return candidates[0]


def fonts() -> dict:
    # 顺序按"Windows 上更可能存在"排：雅黑/Consolas 是系统自带，
    # Noto Serif SC 在本机已装（衬线标题靠它），HarmonyOS/JetBrains 只作兜底
    return {
        "serif": pick_font(["Noto Serif SC", "Source Han Serif SC", "SimSun",
                            "宋体", "Microsoft YaHei UI"]),
        "sans": pick_font(["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                           "HarmonyOS Sans SC", "Segoe UI"]),
        "mono": pick_font(["Cascadia Code", "Consolas", "JetBrains Mono",
                           "Courier New"]),
    }


# --------------------------------------------------------------------------
# 样式表
# --------------------------------------------------------------------------
_QSS = """
* {{ outline: none; }}
QWidget {{
    color: {ink};
    font-family: "{sans}";
    font-size: 13px;
}}
QMainWindow, QWidget#central, QWidget#mainCol {{ background: {paper}; }}

/* ---------------- 侧栏 ---------------- */
QFrame#side {{
    background: {paper2};
    border-right: 1px solid {line};
}}
QLabel#brandName {{
    font-family: "{serif}";
    font-size: 17px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#brandSub {{
    color: {muted2};
    font-size: 10px;
    letter-spacing: 2px;
}}
QLabel#seal {{
    border: 1.5px solid {moss};
    border-radius: 13px;
    background: transparent;
}}
QLabel#sealCore {{
    background: {moss};
    border-radius: 4px;
}}
QLabel#sessionTitle {{ font-size: 12.5px; font-weight: 500; color: {ink}; }}
QLabel#sessionMeta {{ font-size: 10.5px; color: {muted2}; }}
QPushButton#newChat {{
    background: {surface};
    border: 1px solid {line2};
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#newChat:hover {{ background: {moss_tint}; border-color: {moss}; }}
QPushButton#newChat:pressed {{ background: {paper2}; }}

QLineEdit#search {{
    background: {search_bg};
    border: 1px solid {line};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12.5px;
    color: {ink};
}}
QLineEdit#search:focus {{ background: {surface}; border-color: {moss}; }}

QListWidget#sessionList {{
    background: transparent;
    border: 0;
    outline: 0;
}}
QListWidget#sessionList::item {{
    color: {ink};
    border-radius: 8px;
    padding: 8px 10px;
    margin: 1px 0;
}}
QListWidget#sessionList::item:hover {{ background: {hover}; }}
QListWidget#sessionList::item:disabled {{
    color: {muted2};
    font-size: 10.5px;
    padding: 6px 8px 2px;
}}
QListWidget#sessionList::item:selected {{
    background: {sel_bg};
    color: {ink};
    border-left: 2px solid {moss};
}}
QLabel#grpLabel {{
    color: {muted2};
    font-size: 10.5px;
    letter-spacing: 2px;
    padding: 10px 6px 4px;
}}
QFrame#sideFoot {{ border-top: 1px solid {line}; }}
QPushButton#sideRow {{
    text-align: left;
    padding: 7px 8px;
    border: 0;
    border-radius: 8px;
    color: {ink2};
    font-size: 12.5px;
}}
QPushButton#sideRow:hover {{ background: {hover}; }}
/* 侧栏这几项都挂 QMenu，默认会画一个 ▾，在 264px 宽里很碍眼 */
QPushButton#sideRow::menu-indicator {{ image: none; width: 0; height: 0; right: 0; }}
QLabel#sideMeta {{ color: {muted2}; font-size: 11px; }}

/* ---------------- 顶栏 ---------------- */
QFrame#top {{
    background: {top_bg};
    border-bottom: 1px solid {line};
}}
QLabel#title {{ font-size: 14.5px; font-weight: 600; }}
QLabel#sub {{ color: {muted2}; font-size: 11px; }}

QPushButton#modelBtn {{
    background: {surface};
    border: 1px solid {line2};
    border-radius: 16px;
    padding: 5px 12px;
    font-family: "{mono}";
    font-size: 12px;
    color: {ink2};
}}
QPushButton#modelBtn:hover {{ border-color: {moss}; }}
QPushButton#modelBtn::menu-indicator {{ image: none; width: 0; }}

QPushButton#iconBtn {{
    background: transparent;
    border: 0;
    border-radius: 8px;
    padding: 5px;
}}
QPushButton#iconBtn:hover {{ background: {paper2}; }}

/* ---------------- 消息区 ---------------- */
QScrollArea {{ background: {paper}; border: 0; }}
QWidget#feed {{ background: {paper}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0; }}
QScrollBar::handle:vertical {{ background: {line2}; border-radius: 4px; min-height: 36px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QLabel#metaLbl {{ color: {muted2}; font-size: 11px; }}
QLabel#bubbleTxt {{ background: transparent; }}

/* 用户：右对齐气泡，右下角收窄 */
QFrame#bubbleME {{
    background: {moss_tint};
    border: 1px solid {user_line};
    border-top-left-radius: 18px;
    border-top-right-radius: 18px;
    border-bottom-right-radius: 4px;
    border-bottom-left-radius: 18px;
}}
/* AI：无气泡，左侧一道墨绿细线 */
QFrame#bubbleAI {{
    background: transparent;
    border: 0;
    border-left: 2px solid {moss};
}}

/* 系统 / 错误：左对齐内联卡片（不再居中窄条） */
QFrame#sysChip {{
    background: {surface};
    border: 1px solid {line};
    border-radius: 10px;
}}
QFrame#errChip {{
    background: {seal_tint};
    border: 1px solid {err_line};
    border-radius: 10px;
}}
QFrame#errChip QLabel, QFrame#sysChip QLabel {{ color: {ink2}; }}

/* ---------------- 输入区 ---------------- */
QFrame#composerWrap {{ background: {paper}; }}
QFrame#composerCard {{
    background: {surface};
    border: 1px solid {line2};
    border-radius: 16px;
}}
QFrame#composerCard:focus-within {{ border-color: {moss}; }}
QFrame#chip {{
    background: {paper2};
    border: 0;
    border-radius: 8px;
}}
QTextEdit#input {{
    background: transparent;
    border: 0;
    padding: 2px 2px 0 2px;
    font-size: 14px;
    color: {ink};
    selection-background-color: {moss_tint};
    selection-color: {ink};
}}
QLabel#hint {{ color: {muted2}; font-size: 11px; }}

QPushButton#copyBtn {{
    background: transparent;
    border: 0;
    border-radius: 7px;
    padding: 5px 9px;
    color: {muted};
    font-size: 12px;
}}
QPushButton#copyBtn:hover {{ background: {paper2}; color: {ink}; }}
QPushButton#copyBtn:checked {{
    background: {moss_tint};
    color: {moss};
    border: 1px solid {moss};
}}
/* 消息操作条：悬停才出现的小按钮，弱化到不抢正文 */
QWidget#acts {{ background: transparent; }}
QPushButton#actBtn {{
    background: transparent;
    border: 0;
    border-radius: 7px;
    padding: 3px 8px;
    color: {muted2};
    font-size: 11.5px;
}}
QPushButton#actBtn:hover {{ background: {paper2}; color: {ink}; }}
QPushButton#send {{
    background: {moss};
    border: 0;
    border-radius: 9px;
}}
QPushButton#send:hover {{ background: {moss_hover}; }}
QPushButton#send:disabled {{ background: {line2}; }}

/* ---------------- 空状态 ---------------- */
QFrame#welcome {{
    background: transparent;
    border: 0;
}}
QLabel#wTitle {{ font-family: "{serif}"; font-size: 24px; font-weight: 600; }}
QLabel#wSub {{ color: {muted}; font-size: 12.5px; }}
QLabel#wStatus {{ color: {moss}; font-size: 11.5px; }}
QPushButton#starter {{
    background: {surface};
    border: 1px solid {line};
    border-radius: 12px;
    padding: 12px 14px;
    text-align: left;
    font-size: 13px;
    color: {ink};
}}
QPushButton#starter:hover {{
    border-color: {moss};
    background: {moss_tint};
}}
QPushButton#chip {{
    background: {paper2};
    border: 1px solid {line};
    border-radius: 14px;
    padding: 6px 12px;
    font-size: 12px;
    color: {ink2};
}}
QPushButton#chip:hover {{ background: {moss_tint}; border-color: {moss}; }}

/* ---------------- 模型浮层 ---------------- */
QFrame#modelPop {{
    background: {surface};
    border: 1px solid {line};
    border-radius: 12px;
}}
QLabel#popHead {{ color: {muted2}; font-size: 11px; padding: 2px 4px; }}
QLabel#popName {{ font-family: "{mono}"; font-size: 12.5px; color: {ink}; }}
QLabel#popSize {{ font-size: 11px; color: {muted2}; }}
QLabel#popTick {{ color: {moss}; font-size: 12px; font-weight: 700; }}
QLabel#popTip {{ color: {muted2}; font-size: 10.5px; padding: 6px 4px 2px; }}
QWidget#modelRow {{
    background: transparent;
    border-radius: 8px;
}}
QWidget#modelRow:hover {{ background: {hover}; }}
QScrollArea {{ background: transparent; }}

/* ---------------- 菜单 / 提示 ---------------- */
QMenu {{
    background: {surface};
    border: 1px solid {line};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 26px 7px 12px;
    border-radius: 6px;
    font-size: 12.5px;
    color: {ink};
}}
QMenu::item:selected {{ background: {moss_tint}; color: {ink}; }}
QMenu::item:disabled {{ color: {muted2}; font-size: 11px; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 5px 8px; }}
QToolTip {{
    background: {ink};
    color: {paper};
    border: 0;
    padding: 5px 8px;
    font-size: 11.5px;
}}
"""


def qss(theme: str = "paper") -> str:
    t = dict(THEMES.get(theme, PAPER))
    f = fonts()
    t.update(f)
    # QSS 里 { } 需要转义，模板用 {{ }} 表示字面量
    return _QSS.format(**t)
