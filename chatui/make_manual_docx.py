# -*- coding: utf-8 -*-
"""生成《离线 AI 聊天工具 · 接收方使用说明》Word 文档（.docx）。
纯离线生成，使用 python-docx，不依赖任何本地文档服务。
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# 输出目录取"当前实际存在的构建产物"：优先 dist/offline_chat（spec 默认名），
# 兼容旧名 offline_chat_portable；都没有就落到 dist/offline_chat。
_BASE = os.path.dirname(os.path.abspath(__file__))
_DIST = os.path.join(_BASE, "dist")
_CANDIDATES = [os.path.join(_BASE, "dist_new", "offline_chat"),
               os.path.join(_DIST, "offline_chat"),
               os.path.join(_DIST, "offline_chat_portable")]
OUT_DIR = next((d for d in _CANDIDATES if os.path.isdir(d)), _CANDIDATES[0])
OUT = os.path.join(OUT_DIR, "离线AI聊天工具_使用说明.docx")

CJK = "微软雅黑"          # 中文正文/标题主字体（系统通用，WPS/Word 均支持）
CJK_MONO = "Consolas"     # 路径/命令等代码用等宽，但中文回退到雅黑
ACCENT = RGBColor(0x1F, 0x4E, 0x79)   # 深蓝强调
ACCENT2 = RGBColor(0x2E, 0x75, 0xB6)
WARN_BG = "FDECEA"        # 注意框浅红底
TIP_BG = "EAF3FB"         # 提示框浅蓝底
HDR_BG = "1F4E79"         # 表头深蓝底


def set_cjk(run, font=CJK, size=None, bold=None, color=None):
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), font)
    rfonts.set(qn('w:ascii'), font)
    rfonts.set(qn('w:hAnsi'), font)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def shade_cell(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    tcpr.append(shd)


def shade_paragraph(par, fill):
    ppr = par._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    ppr.append(shd)


def add_heading(doc, text, level=1):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14 if level == 1 else 10)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.keep_with_next = True
    if level == 1:
        set_cjk(p.add_run(text), CJK, 15, True, ACCENT)
        # 蓝色下边框
        ppr = p._p.get_or_add_pPr()
        pbdr = OxmlElement('w:pBdr')
        bottom = OxmlElement('w:bottom')
        bottom.set(qn('w:val'), 'single')
        bottom.set(qn('w:sz'), '6')
        bottom.set(qn('w:space'), '2')
        bottom.set(qn('w:color'), '2E75B6')
        pbdr.append(bottom)
        ppr.append(pbdr)
    else:
        set_cjk(p.add_run(text), CJK, 12.5, True, ACCENT2)
    return p


def add_para(doc, text, size=10.5, indent=True, bold=False, color=None, space_after=4):
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.25
    p.paragraph_format.space_after = Pt(space_after)
    if indent:
        p.paragraph_format.first_line_indent = Pt(size * 2)
    set_cjk(p.add_run(text), CJK, size, bold, color)
    return p


def add_bullet(doc, text, size=10.5):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.line_spacing = 1.2
    p.paragraph_format.space_after = Pt(2)
    set_cjk(p.add_run(text), CJK, size)
    return p


def add_number(doc, text, size=10.5):
    p = doc.add_paragraph(style='List Number')
    p.paragraph_format.line_spacing = 1.2
    p.paragraph_format.space_after = Pt(2)
    set_cjk(p.add_run(text), CJK, size)
    return p


def add_callout(doc, title, body, fill=TIP_BG):
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = tbl.cell(0, 0)
    shade_cell(cell, fill)
    # 左边框强调
    tcpr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement('w:tcBorders')
    left = OxmlElement('w:left')
    left.set(qn('w:val'), 'single')
    left.set(qn('w:sz'), '18')
    left.set(qn('w:space'), '0')
    left.set(qn('w:color'), '2E75B6' if fill == TIP_BG else 'C0392B')
    borders.append(left)
    tcpr.append(borders)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    set_cjk(p.add_run(title), CJK, 10.5, True, ACCENT if fill == TIP_BG else RGBColor(0xC0, 0x39, 0x2B))
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(0)
    set_cjk(p2.add_run(body), CJK, 10)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return tbl


def add_table(doc, headers, rows, widths=None):
    tbl = doc.add_table(rows=1, cols=len(headers))
    tbl.style = 'Table Grid'
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = tbl.rows[0].cells
    for i, h in enumerate(headers):
        shade_cell(hdr[i], HDR_BG)
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_cjk(p.add_run(h), CJK, 10, True, RGBColor(0xFF, 0xFF, 0xFF))
    for r in rows:
        cells = tbl.add_row().cells
        for i, val in enumerate(r):
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            set_cjk(p.add_run(val), CJK, 9.5)
    if widths:
        for row in tbl.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return tbl


def main():
    doc = Document()
    # 默认正文字体（西文回退）
    style = doc.styles['Normal']
    style.font.name = CJK
    style.element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    style.font.size = Pt(10.5)

    # ---------- 封面 ----------
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.paragraph_format.space_before = Pt(60)
    set_cjk(t.add_run("离线 AI 聊天工具"), CJK, 26, True, ACCENT)
    t2 = doc.add_paragraph()
    t2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t2.paragraph_format.space_before = Pt(6)
    set_cjk(t2.add_run("接收方使用说明"), CJK, 18, True, ACCENT2)
    t3 = doc.add_paragraph()
    t3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t3.paragraph_format.space_before = Pt(16)
    set_cjk(t3.add_run("10 个模型 · 文档问答 · 语音输入 · 深度思考 · 无需联网 · 数据不出本机"),
            CJK, 11, False, RGBColor(0x66, 0x66, 0x66))
    t4 = doc.add_paragraph()
    t4.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t4.paragraph_format.space_before = Pt(4)
    set_cjk(t4.add_run("版本 v3.0　·　2026-10-18"), CJK, 9.5, False,
            RGBColor(0x88, 0x88, 0x88))
    doc.add_paragraph()

    add_callout(doc, "一句话说明",
               "这是一个把 AI 大模型打包好的离线聊天软件。把它解压后双击即可使用，"
               "全程不需要网络、不需要下载、不需要注册账号，也不会把你的任何对话内容传到外面。"
               "除了文字聊天，它还能看懂图片、读你的文档（PDF/Word/Excel/PPT）、听你说话并朗读回答。",
               TIP_BG)

    # ---------- 1. 简介 ----------
    add_heading(doc, "一、软件简介", 1)
    add_para(doc, "本工具将开源大语言模型（LLaMA、Qwen、Gemma、DeepSeek 等）与本地推理引擎（Ollama）"
                  "一同打包，做成一个开箱即用的桌面聊天程序。它的核心特点：")
    add_bullet(doc, "完全离线：所有模型已随压缩包内置，首次运行自动导入，之后永不联网。")
    add_bullet(doc, "隐私安全：对话只在你自己的电脑上进行，不上传任何服务器。")
    add_bullet(doc, "零配置：不需要 Python、不需要显卡驱动、不需要命令行。")
    add_bullet(doc, "内置 10 个模型：覆盖通用对话、代码编写、轻量推理，并额外包含一个可看图的视觉模型。")
    add_bullet(doc, "会读文档：PDF / Word / Excel / PPT / ePub / 图片拖进来就能问答，"
                    "扫描件自动 OCR，长文档自动检索并标注出处（[1][2]）。")
    add_bullet(doc, "会听会说：点麦克风用语音提问（本地识别），AI 的回答可以点“朗读”念出来。")
    add_bullet(doc, "想得更周全：“深度思考”模式先写草稿、再自我挑刺、最后重写定稿。")
    add_bullet(doc, "好用：多轮追问、Markdown 排版、会话自动保存与多会话管理、长期记忆、"
                    "停止生成、拖拽/粘贴图片、参数调节。")

    # ---------- 2. 系统要求 ----------
    add_heading(doc, "二、运行环境要求", 1)
    add_table(doc,
              ["项目", "最低要求", "建议配置"],
              [
                  ["操作系统", "Windows 10 / 11 64 位", "Windows 10/11 64 位"],
                  ["内存", "4 GB", "8 GB 及以上"],
                  ["硬盘空闲", "系统盘（C 盘）约 15 GB*", "系统盘 30 GB 以上"],
                  ["显卡", "无需独显（纯 CPU 运行）", "有独显也可，但本版未启用"],
                  ["其他", "需解压软件（如 WinRAR/7-Zip）", "任意现代解压工具"],
              ],
              widths=[3.2, 5.0, 5.0])
    add_callout(doc, "关于硬盘空间的说明",
               "* 压缩包约 17 GB，解压后程序文件夹约 18 GB。首次运行时，程序会把 10 个模型复制到系统盘的用户目录"
               "（C:\\Users\\你的用户名\\.ollama），这一步会额外占用约 15 GB，因此系统盘最好留出 30 GB 空闲。"
               "导入完成后，原压缩包和程序目录里的模型副本可保留也可删除（见第七部分）。",
               TIP_BG)

    # ---------- 3. 文件清单 ----------
    add_heading(doc, "三、你收到的文件", 1)
    add_para(doc, "发送方会把下面这个压缩包传给你：")
    add_bullet(doc, "offline_chat.zip（约 17 GB）—— 解压后是完整的离线程序。")
    add_para(doc, "解压后会得到一个文件夹 offline_chat，里面的结构如下（请勿手动改动）：")
    add_table(doc,
              ["文件 / 文件夹", "作用"],
              [
                  ["offline_chat.exe", "主程序，双击它启动聊天窗口"],
                  ["_internal/", "程序运行所需的全部依赖（含 Ollama 引擎与 10 个模型，含视觉投影），必须和 exe 放在一起"],
                  ["diagnose.bat", "一键诊断：装不上 / 起不来时双击它，会自动生成诊断日志"],
                  ["start_models_on_D.bat", "C 盘空间不足时用它启动，可把模型改存到其他盘"],
                  ["uninstall_offline_chat.bat", "彻底卸载：删除程序、模型、环境变量等全部痕迹"],
                  ["离线AI聊天工具_使用说明.docx", "本说明书"],
              ],
              widths=[4.5, 8.7])

    # ---------- 4. 安装与首次运行 ----------
    add_heading(doc, "四、安装与首次运行（跟着做即可）", 1)
    add_callout(doc, "解压务必用 7-Zip / WinRAR",
               "请勿用 Windows 资源管理器自带的“解压全部”——它对 14GB 这种超大压缩包经常解压不全、"
               "甚至静默损坏文件，会导致后续模型导入失败。请安装 7-Zip 或 WinRAR，右键压缩包 → "
               "“解压到 offline_chat\\” 即可。", WARN_BG)
    add_number(doc, "把 offline_chat.zip 解压到一个简单的路径，例如 D:\\offline_chat "
                    "（建议纯英文、不要放在太深的文件夹里，也不要放在中文/带空格的路径，可减少意外问题）。")
    add_callout(doc, "若 C 盘空间不足 15 GB，请先看这里",
               "模型默认导入系统盘（C 盘），需要约 15 GB 空闲；空间不够会出现“小模型装上了、大模型装不上”。"
               "此时请不要直接双击 offline_chat.exe，而是双击文件夹里的 start_models_on_D.bat——"
               "它会自动把模型改存到程序所在的盘（如 D 盘），再启动程序。", WARN_BG)
    add_number(doc, "打开解压出来的 offline_chat 文件夹，双击其中的 offline_chat.exe。")
    add_number(doc, "首次启动会弹出聊天窗口，并在顶部状态栏显示“首次导入内置模型…”和“导入模型 3/10 · 名称”的进度，"
                    "程序正在把你内置的 10 个模型导入到本地。这一步在普通电脑上大约需要十几分钟，"
                    "请耐心等待，期间不要关闭窗口。")
    add_number(doc, "当状态栏提示“Ollama 在线”、模型下拉框可以选择模型时，说明导入完成，可以开始聊天了。")
    add_number(doc, "以后再次使用，直接双击 offline_chat.exe 即可，无需重复导入，也无需联网。"
                    "程序只允许同时运行一个窗口，重复双击会提示“程序已经在运行中”。")
    add_callout(doc, "杀毒软件提示？",
               "部分杀毒软件可能把内置的 ollama.exe 误报为风险程序。这是误报——该引擎是开源推理程序，"
               "不联网、不收集信息。若被拦截，请在杀毒软件中将 offline_chat 文件夹添加为信任/白名单后重试。",
               WARN_BG)

    # ---------- 5. 日常使用 ----------
    add_heading(doc, "五、怎么用：文字、追问与图片", 1)
    add_para(doc, "界面分三块：左边是会话列表（264px），中间是对话区，底部是输入框。"
                  "首次打开是空状态，点任意一条示例就能开始。")
    add_table(doc,
              ["位置", "按钮", "作用"],
              [
                  ["左侧栏", "＋ 新建对话", "开一个新会话；旧会话自动保存在左边列表里"],
                  ["左侧栏", "搜索框", "按标题过滤会话，不用翻列表"],
                  ["左侧栏", "会话列表", "点一下切换；右键可置顶 / 重命名 / 导出 Markdown / 删除"],
                  ["左侧栏", "记忆", "让 AI 记住你的偏好与背景；查看、删除记忆；开关记忆注入"],
                  ["左侧栏", "设置", "调整回答随机性（temperature）、上下文长度、系统提示词"],
                  ["顶栏", "模型名", "点一下弹出模型列表（含体积），切换时自动释放上一个模型"],
                  ["顶栏", "下载图标", "把当前会话导出成 Markdown"],
                  ["顶栏", "⋯ 更多", "新建 / 清空当前会话 / 夜读模式 / 设置"],
                  ["输入框下方", "文档", "选择 PDF/Word/Excel/PPT 等文件问答（也可直接拖进窗口）"],
                  ["输入框下方", "语音", "点一下开始说话，再点一下结束并转成文字"],
                  ["输入框下方", "深度", "开启深度思考：草稿 → 自检 → 定稿（约 3 倍耗时）"],
                  ["输入框右下", "发送 / 停止", "回车发送；生成中变成方块，点它或按 Esc 停止"],
              ],
              widths=[3.0, 3.4, 7.0])
    add_callout(doc, "两个小细节",
               "① 回答里的 [1][2] 可以直接点，会显示这段内容来自文档的哪一处；\n"
               "② 长回答生成时末尾有一条竖线（▍）表示还在写，停下或写完会自动消失。",
               TIP_BG)
    add_number(doc, "选模型：点顶栏的模型名。默认 gemma3:4b（综合最稳）；写代码选 qwen2.5-coder:3b；"
                    "看图片必须选 gemma3:4b-vision。")
    add_number(doc, "输入问题，按回车发送（Shift+Enter 换行）。回答会逐字出现，"
                    "代码块、列表、加粗文字会自动排版。")
    add_number(doc, "继续追问即可：程序会自动带上之前的对话，提示栏会显示"
                    "“上下文 ~N/6144”，让你知道还能聊多长。")
    add_number(doc, "生成过程中如果不想等了，点输入框右侧的红色方块，或按 Esc 停止；"
                    "已经生成的内容会保留。")
    add_number(doc, "图片问答：把图片直接拖进窗口，或按 Ctrl+V 粘贴截图，输入框上方会出现缩略图，"
                    "写好问题后发送即可。")
    add_number(doc, "调参数：点左下角“设置”，可调整回答的随机性（temperature）、上下文长度和系统提示词，"
                    "修改后会自动记住，下次打开仍然生效。")
    add_number(doc, "会话会自动保存：关闭程序再打开，最近的对话会自动恢复；按 Ctrl+L 可清空并重新开始。")
    add_number(doc, "复制：鼠标移到消息上点“复制”；右键复制按钮还能选“复制 Markdown”或“复制纯文本”。")
    add_callout(doc, "使用小贴士",
               "• 提问越具体、上下文越清楚，回答质量越高。\n"
               "• 代码类问题建议选用 qwen2.5-coder:3b。\n"
               "• 图片问答一定要切到 gemma3:4b-vision，否则会提示“当前模型不支持图像输入”。\n"
               "• 如果回答太长或被截断，可接着追问“继续”。\n"
               "• 设置里的“上下文”请避开 4096（qwen3 系列在该值下可能输出乱码），默认 6144 即可。\n"
               "• 全部计算在本地 CPU 完成，速度比云端略慢属正常现象。",
               TIP_BG)

    # ---------- 6. 进阶功能 ----------
    add_heading(doc, "六、进阶功能：文档问答、语音、深度思考、多会话", 1)

    add_heading(doc, "6.1 让 AI 读你的文档（PDF / Word / Excel / PPT / ePub）", 2)
    add_number(doc, "把文件直接拖进聊天窗口，或点输入框下方的“文档”按钮选择文件；"
                    "输入框上方会出现文件名，写好后按回车发送。")
    add_number(doc, "短文档（3000 字以内）会整篇随消息一起交给模型；长文档会自动切成片段入库，"
                    "只把最相关的几段交给模型，回答里会用 [1][2] 标注出处。")
    add_number(doc, "扫描版 PDF 和图片里的文字会自动用本地 OCR 识别（不需要联网），"
                    "图片附件上还可以点“提取文字”把识别结果填进输入框。")
    add_callout(doc, "小提示",
               "• 支持：PDF、docx、xlsx、pptx、epub、txt/md/csv/json/py 等，以及 png/jpg 图片。\n"
               "• 单个文件上限 50MB；扫描件最多识别前 10 页。\n"
               "• 第一次识别文字时会加载本地 OCR 模型，大约需要几秒钟。",
               TIP_BG)

    add_heading(doc, "6.2 语音输入与朗读", 2)
    add_number(doc, "点输入框下方的麦克风按钮开始说话，再点一次结束，"
                    "程序会用本地语音识别把话转成文字填进输入框，确认无误后发送。")
    add_number(doc, "把鼠标移到 AI 的回答上，点“朗读”即可用本地语音念出来（再点一次停止）。")
    add_para(doc, "语音识别和朗读全都在本机完成，不使用任何云端服务；"
                  "识别模型约 145MB、朗读模型约 63MB，已随程序内置。", size=9.5,
             color=RGBColor(0x66, 0x66, 0x66))
    add_callout(doc, "点麦克风没反应？",
               "程序会先检查麦克风能不能用：没有设备、被其他程序占用、或系统麦克风权限被关闭时，"
               "会在对话区给出具体原因和处理办法（例如“麦克风被占用”“隐私设置未授权”），"
               "按提示在「设置 → 系统 → 声音 → 输入」里选好设备再试即可。"
               "语音识别本身不联网，识别失败不影响打字聊天。", WARN_BG)

    add_heading(doc, "6.3 深度思考（更慢但更周全）", 2)
    add_para(doc, "点输入框下方的“深度”按钮开启后，每一轮会先写一版草稿，"
                  "再让模型自己挑毛病（漏了什么、哪里不对），最后按意见重写一遍定稿。"
                  "适合写方案、总结、需要少出错的场合；代价是耗时约为普通的 3 倍。")

    add_heading(doc, "6.4 多会话与长期记忆", 2)
    add_number(doc, "左侧栏的会话列表：可以新建多个会话，各自独立保存；支持置顶、重命名、删除、"
                    "以及导出成 Markdown 文件存档。")
    add_number(doc, "左下角“记忆”：让 AI 记住你的偏好和背景（例如“回答尽量简短”）"
                    "，之后每轮对话都会带上；也可以查看全部记忆或让 AI 忘掉某一条。")
    add_number(doc, "按 Ctrl+L 只清空当前会话；换会话不会影响其他会话的历史。")

    add_heading(doc, "6.5 给其他软件用（OpenAI 接口 / 定时任务）", 2)
    add_para(doc, "技术用户还可以在程序目录里运行：")
    add_bullet(doc, "python server\\gateway.py —— 开启 OpenAI 兼容网关，"
                    "其他支持 ChatGPT 接口的软件把地址填 http://127.0.0.1:8080/v1 即可连本机模型。")
    add_bullet(doc, "python server\\scheduler.py —— 定时让本地模型干活（每 N 分钟或每天几点），"
                    "结果记在 schedule.log。")

    # ---------- 7. 内置模型一览 ----------
    add_heading(doc, "七、内置 10 个模型一览", 1)
    add_table(doc,
              ["模型名称", "体积", "适合做什么"],
              [
                  ["gemma3:4b", "约 2.4 GB", "默认推荐，综合对话、写作、翻译均衡、输出稳定"],
                  ["gemma3:4b-vision", "约 3.3 GB", "看图问答：识别图片内容、颜色、文字（由 gemma3:4b + 视觉投影组成）"],
                  ["qwen3:4b", "约 2.4 GB", "中文理解与推理，回答较详细"],
                  ["qwen3:1.7b", "约 1.8 GB", "轻量快速，日常闲聊与简单问答"],
                  ["qwen3:0.6b", "约 0.6 GB", "极速，适合低配机器简单任务"],
                  ["qwen2.5-coder:3b", "约 2.0 GB", "代码编写、解释、调试"],
                  ["deepseek-r1:1.5b", "约 1.1 GB", "逻辑推理、数学题（会展示思考过程）"],
                  ["llama3.2:3b", "约 1.9 GB", "英文对话、摘要、通用任务"],
                  ["llama3.2:1b", "约 0.8 GB", "极轻量英文任务"],
                  ["gemma3:1b", "约 0.8 GB", "轻量多语言任务"],
              ],
              widths=[4.2, 2.6, 6.4])
    add_para(doc, "注：由于所有模型均为纯 CPU 推理，体积越小的模型响应越快；"
                  "若电脑配置较低，优先选用 1b/1.7b 这类小模型即可流畅使用。",
             size=9.5, color=RGBColor(0x66,0x66,0x66))

    # ---------- 7. 空间回收 ----------
    add_heading(doc, "八、导入完成后如何回收磁盘空间", 1)
    add_para(doc, "模型导入后，它们已经保存在本地模型库中（默认是系统盘的 C:\\Users\\你的用户名\\.ollama；"
                  "若你用的是 start_models_on_D.bat，则在程序所在盘的 ollama_models 目录），"
                  "与解压目录里的那份副本是两份。如果你想腾出空间，可以：")
    add_number(doc, "确认程序已经成功导入过模型（能正常聊天即代表成功）。")
    add_number(doc, "删除解压目录 offline_chat 里的 _internal\\bundle_models 文件夹"
                    "（约 15 GB）。模型已在 .ollama 中，删除后不影响使用。")
    add_number(doc, "如不再需要发送/备份，原 offline_chat.zip 压缩包也可删掉。")
    add_callout(doc, "千万注意",
               "不要只复制 offline_chat.exe 单独发给别人或移到别处——它必须和 _internal 文件夹一起移动，"
               "否则程序无法运行。移动时请整体移动整个 offline_chat 文件夹。",
               WARN_BG)

    # ---------- 8. 常见问题 ----------
    add_heading(doc, "九、常见问题与解决办法", 1)
    add_table(doc,
              ["现象", "可能原因 / 解决办法"],
              [
                  ["双击 exe 没反应", "可能被杀毒软件拦截，请加白名单；或确认已完整解压（不是只复制了 exe）。"],
                  ["首次启动很久没进界面", "正在导入模型，属正常，耐心等待十几分钟，勿关闭；顶部会显示导入进度。"],
                  ["提示“连接 Ollama 失败 / 404”", "多为 _internal 文件夹丢失或被误删。请重新解压完整文件夹，保持 exe 与 _internal 在一起。"],
                  ["回答很慢", "纯 CPU 推理正常现象；换用更小的模型（如 1b/1.7b）可明显提速。"],
                  ["发图片提示“不支持图像输入”", "当前模型不是视觉模型。请点顶栏模型名切到 gemma3:4b-vision 后重试。"],
                  ["回答是一串 0000 或乱码",
                   "多为内存被多个模型占满（本机 16GB，两个模型同时驻留就会这样）。处理：① 换一个模型再切回来——"
                   "程序会自动卸载上一个模型，通常立刻恢复；② 关闭浏览器多标签、PS、IDE 等大程序；"
                   "③ 检查“设置”里的上下文是否为 4096，qwen3 系列请用 6144。"],
                  ["想找回之前的对话", "会话已自动保存，重新打开程序即可恢复；按 Ctrl+L 清空。"],
                  ["语音输入没反应 / 提示“无法打开录音设备”",
                   "① Windows 设置 → 系统 → 声音 → 输入，确认已选择麦克风且音量条有跳动；"
                   "② 设置 → 隐私和安全性 → 麦克风，允许桌面应用访问；"
                   "③ 关掉占用麦克风的会议软件（腾讯会议/钉钉等）后重试。"],
                  ["录音时提示“没收到任何音频”",
                   "多为麦克风被其他程序独占、系统麦克风权限被关，或该设备实际未接入（例如只剩“立体声混音”）。"
                   "换一个输入设备后重新点麦克风即可；语音识别与朗读都不联网。"],
                  ["系统盘空间不足", "清理出约 15 GB 空闲后再首次运行；或用 start_models_on_D.bat 把模型改存到其他盘（见第十部分）。"],
                  ["想卸载", "双击 uninstall_offline_chat.bat 一键彻底清除（程序、模型、环境变量、快捷方式，见第十部分）；也可手动删除 offline_chat 文件夹与 C:\\Users\\你的用户名\\.ollama。"],
              ],
              widths=[4.2, 9.0])

    # ---------- 9. 仍无法安装时的排查 ----------
    add_heading(doc, "十、如果仍然无法正常安装 / 启动", 1)
    add_para(doc, "极少数情况下可能遇到安装失败（如被杀毒软件拦截、解压不完整）。请按顺序排查：")
    add_number(doc, "确认解压完整：打开 offline_chat\\_internal\\bundle_models，应能看到 10 个以 .gguf 结尾的模型文件"
                    "（每个几百 MB 到 2~3 GB），以及 gemma3-4b-vision 目录下的 mmproj 视觉投影文件（约 850 MB）。"
                    "若某文件大小为 0 或缺失，说明解压不完整，请用 7-Zip 重新解压。")
    add_number(doc, "关闭杀毒软件拦截：本工具内置的 ollama.exe 是开源推理程序，但部分杀软/系统 SmartScreen 会误报。"
                    "请将整个 offline_chat 文件夹加入杀毒软件“白名单/信任区”；若双击被拦截，右键 exe → "
                    "“属性” → 底部“解除锁定”（如有），再运行。")
    add_number(doc, "使用纯英文路径：建议把 offline_chat 文件夹放在如 D:\\offline_chat 这样的纯英文短路径下，"
                    "避免中文或带空格的目录名。")
    add_number(doc, "一键诊断：文件夹内附带 diagnose.bat，双击运行，会自动检测模型文件、启动内置 Ollama、"
                    "尝试导入并生成 diagnose_result.txt 与 serve_diag.log。把这两个文件发给发送给你软件的人，"
                    "即可快速定位问题。")
    add_number(doc, "系统盘空间：导入需要 C 盘约 15 GB 空闲，空间不足会失败，请先清理。")

    # ---------- 10. 辅助工具 ----------
    add_heading(doc, "十一、随包附带的三个辅助工具", 1)
    add_para(doc, "offline_chat 文件夹里还有三个小工具（都是双击即用的批处理文件），"
                  "平时用不到，遇到对应情况时再用：")
    add_table(doc,
              ["工具文件", "什么时候用", "怎么用"],
              [
                  ["diagnose.bat", "装不上、启动不了、模型缺失，需要定位原因时",
                   "双击运行，等它结束；把生成的 diagnose_result.txt 和 serve_diag.log 发给发送方"],
                  ["start_models_on_D.bat", "C 盘空间不足 15 GB，导致大模型装不上时",
                   "双击它（不是双击 exe）来启动程序；它会先把模型目录改到程序所在盘再启动。"
                   "若提示目标盘也不够，可用记事本把里面的 MODELS_DRIVE 改成其它盘符"],
                  ["uninstall_offline_chat.bat", "想彻底卸载、清空使用痕迹时",
                   "把它放进 offline_chat 文件夹里双击；核对清单后输入 Y。"
                   "它会弹出一个新窗口执行删除（正常现象），完成后重启一次电脑"],
              ],
              widths=[4.4, 4.4, 4.6])
    add_callout(doc, "卸载小提示",
               "uninstall_offline_chat.bat 会删除：程序文件夹（含内置模型）、便携版 Ollama、"
               "C 盘模型库、程序所在盘的模型库、OLLAMA_MODELS 环境变量、日志以及同名快捷方式。"
               "若你的电脑另外安装过官方 Ollama，脚本会自动跳过官方的模型库不清除，避免误删你自己的模型。",
               TIP_BG)

    # ---------- 十二、版本与更新 ----------
    add_heading(doc, "十二、版本与更新记录", 1)
    add_table(doc,
              ["版本", "日期", "更新内容"],
              [
                  ["v3.0", "2026-10-18",
                   "全新界面：左侧会话栏（搜索/置顶/右键管理）、墨绿纸感主题与夜读模式、无气泡回答、"
                   "模型浮层（含体积）、回答中的 [1] 可点看来源、生成中光标；说明书按新界面重写。"],
                  ["v2.0", "2026-10-18",
                   "新增文档问答（PDF/Word/Excel/PPT/ePub/图片 OCR）、语音输入与朗读、深度思考、"
                   "多会话管理、长期记忆；修复语音设备识别与提示；说明书补充界面速览与语音排查。"],
                  ["v1.1", "2026-10-17",
                   "多轮对话、停止生成、Markdown 排版、会话自动保存、参数面板、图片理解、"
                   "单实例运行、错误重试。"],
                  ["v1.0", "2026-10-16", "首个可分发包：10 个内置模型（含视觉模型）、便携 Ollama、一键导入。"],
              ],
              widths=[1.8, 3.0, 8.8])
    add_para(doc, "查找版本：顶栏右侧显示当前模型，输入框下方提示行显示上下文用量；"
                  "本说明书文件名后的日期即对应版本。",
             size=9.5, color=RGBColor(0x66, 0x66, 0x66))

    # ---------- 结尾 ----------
    doc.add_paragraph()
    end = doc.add_paragraph()
    end.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cjk(end.add_run("本软件完全离线运行，请放心使用。如有疑问，联系发送给你此压缩包的人即可。"),
            CJK, 9.5, False, RGBColor(0x88, 0x88, 0x88))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc.save(OUT)
    print("SAVED:", OUT)

    # 同步一份到 docs/：避免仓库里留着一份几个月前的旧说明书，打开时看不出改没改
    mirror = os.path.join(os.path.dirname(_BASE), "docs", os.path.basename(OUT))
    try:
        os.makedirs(os.path.dirname(mirror), exist_ok=True)
        doc.save(mirror)
        print("MIRROR:", mirror)
    except Exception as e:
        print("MIRROR 失败（不影响分包）:", e)


if __name__ == "__main__":
    main()
