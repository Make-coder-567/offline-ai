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

OUT = r"E:/Workbuddy/2026-10-11-17-27-15/offline-ai/chatui/dist/离线AI聊天工具_使用说明.docx"

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
    set_cjk(t3.add_run("无需联网 · 本地运行 · 数据不出本机"), CJK, 12, False, RGBColor(0x66, 0x66, 0x66))
    doc.add_paragraph()

    add_callout(doc, "一句话说明",
               "这是一个把 AI 大模型打包好的离线聊天软件。把它解压后双击即可使用，"
               "全程不需要网络、不需要下载、不需要注册账号，也不会把你的任何对话内容传到外面。",
               TIP_BG)

    # ---------- 1. 简介 ----------
    add_heading(doc, "一、软件简介", 1)
    add_para(doc, "本工具将开源大语言模型（LLaMA、Qwen、Gemma、DeepSeek 等）与本地推理引擎（Ollama）"
                  "一同打包，做成一个开箱即用的桌面聊天程序。它的核心特点：")
    add_bullet(doc, "完全离线：所有模型已随压缩包内置，首次运行自动导入，之后永不联网。")
    add_bullet(doc, "隐私安全：对话只在你自己的电脑上进行，不上传任何服务器。")
    add_bullet(doc, "零配置：不需要 Python、不需要显卡驱动、不需要命令行。")
    add_bullet(doc, "内置 9 个模型：覆盖通用对话、代码编写、轻量推理等常见场景。")

    # ---------- 2. 系统要求 ----------
    add_heading(doc, "二、运行环境要求", 1)
    add_table(doc,
              ["项目", "最低要求", "建议配置"],
              [
                  ["操作系统", "Windows 10 / 11 64 位", "Windows 10/11 64 位"],
                  ["内存", "4 GB", "8 GB 及以上"],
                  ["硬盘空闲", "系统盘（C 盘）约 15 GB*", "系统盘 20 GB 以上"],
                  ["显卡", "无需独显（纯 CPU 运行）", "有独显也可，但本版未启用"],
                  ["其他", "需解压软件（如 WinRAR/7-Zip）", "任意现代解压工具"],
              ],
              widths=[3.2, 5.0, 5.0])
    add_callout(doc, "关于硬盘空间的说明",
               "* 压缩包本身约 14 GB。首次运行时，程序会把 9 个模型复制到系统盘的用户目录"
               "（C:\\Users\\你的用户名\\.ollama），这一步会额外占用约 14 GB，因此需要系统盘有约 15 GB 空闲。"
               "导入完成后，原压缩包和安装目录可保留也可删除（见第七部分）。",
               TIP_BG)

    # ---------- 3. 文件清单 ----------
    add_heading(doc, "三、你收到的文件", 1)
    add_para(doc, "发送方会把下面这个压缩包传给你：")
    add_bullet(doc, "offline_chat.zip（约 14 GB）—— 这就是全部内容，一个文件搞定。")
    add_para(doc, "解压后会得到一个文件夹 offline_chat，里面的结构如下（请勿手动改动）：")
    add_table(doc,
              ["文件 / 文件夹", "作用"],
              [
                  ["offline_chat.exe", "主程序，双击它启动聊天窗口"],
                  ["_internal/", "程序运行所需的全部依赖（含 Ollama 引擎与 9 个模型文件），必须和 exe 放在一起"],
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
    add_number(doc, "首次启动会弹出聊天窗口，并在底部状态栏显示“首次导入内置模型…”，"
                    "程序正在把你内置的 9 个模型导入到本地。这一步在普通电脑上大约需要几分钟到十几分钟，"
                    "请耐心等待，期间不要关闭窗口。")
    add_number(doc, "当状态栏提示就绪、模型下拉框可以选择模型时，说明导入完成，可以开始聊天了。")
    add_number(doc, "以后再次使用，直接双击 offline_chat.exe 即可，无需重复导入，也无需联网。")
    add_callout(doc, "杀毒软件提示？",
               "部分杀毒软件可能把内置的 ollama.exe 误报为风险程序。这是误报——该引擎是开源推理程序，"
               "不联网、不收集信息。若被拦截，请在杀毒软件中将 offline_chat 文件夹添加为信任/白名单后重试。",
               WARN_BG)

    # ---------- 5. 日常使用 ----------
    add_heading(doc, "五、怎么用：三步开始对话", 1)
    add_number(doc, "在窗口左上角的“模型”下拉框中选择一个模型（默认是 qwen3:4b，综合表现较好）。")
    add_number(doc, "在底部输入框输入你的问题，按回车（或点发送按钮）。")
    add_number(doc, "等待模型生成回复。首次切换某个模型时会先加载它到内存，可能要等几秒到十几秒；"
                    "加载完成后回答会逐字显示。")
    add_callout(doc, "使用小贴士",
               "• 提问越具体、上下文越清楚，回答质量越高。\n"
               "• 代码类问题建议选用 qwen2.5-coder:3b。\n"
               "• 如果回答太长或被截断，可接着追问“继续”。\n"
               "• 全部计算在本地 CPU 完成，速度比云端略慢属正常现象。",
               TIP_BG)

    # ---------- 6. 内置模型一览 ----------
    add_heading(doc, "六、内置 9 个模型一览", 1)
    add_table(doc,
              ["模型名称", "体积", "适合做什么"],
              [
                  ["qwen3:4b", "约 2.4 GB", "默认推荐，综合对话、写作、翻译均衡"],
                  ["qwen3:1.7b", "约 1.8 GB", "轻量快速，日常闲聊与简单问答"],
                  ["qwen3:0.6b", "约 0.6 GB", "极速，适合低配机器简单任务"],
                  ["qwen2.5-coder:3b", "约 2.0 GB", "代码编写、解释、调试"],
                  ["deepseek-r1:1.5b", "约 1.1 GB", "逻辑推理、数学题（会展示思考过程）"],
                  ["llama3.2:3b", "约 1.9 GB", "英文对话、摘要、通用任务"],
                  ["llama3.2:1b", "约 0.8 GB", "极轻量英文任务"],
                  ["gemma3:4b", "约 2.4 GB", "多语言对话、知识问答"],
                  ["gemma3:1b", "约 0.8 GB", "轻量多语言任务"],
              ],
              widths=[4.2, 2.6, 6.4])
    add_para(doc, "注：由于所有模型均为纯 CPU 推理，体积越小的模型响应越快；"
                  "若电脑配置较低，优先选用 1b/1.7b 这类小模型即可流畅使用。", size=9.5, color=RGBColor(0x66,0x66,0x66))

    # ---------- 7. 空间回收 ----------
    add_heading(doc, "七、导入完成后如何回收磁盘空间", 1)
    add_para(doc, "模型导入后，它们已经保存在本地模型库中（默认是系统盘的 C:\\Users\\你的用户名\\.ollama；"
                  "若你用的是 start_models_on_D.bat，则在程序所在盘的 ollama_models 目录），"
                  "与解压目录里的那份副本是两份。如果你想腾出空间，可以：")
    add_number(doc, "确认程序已经成功导入过模型（能正常聊天即代表成功）。")
    add_number(doc, "删除解压目录 offline_chat 里的 _internal\\bundle_models 文件夹"
                    "（约 14 GB）。模型已在 .ollama 中，删除后不影响使用。")
    add_number(doc, "如不再需要发送/备份，原 offline_chat.zip 压缩包也可删掉。")
    add_callout(doc, "千万注意",
               "不要只复制 offline_chat.exe 单独发给别人或移到别处——它必须和 _internal 文件夹一起移动，"
               "否则程序无法运行。移动时请整体移动整个 offline_chat 文件夹。",
               WARN_BG)

    # ---------- 8. 常见问题 ----------
    add_heading(doc, "八、常见问题与解决办法", 1)
    add_table(doc,
              ["现象", "可能原因 / 解决办法"],
              [
                  ["双击 exe 没反应", "可能被杀毒软件拦截，请加白名单；或确认已完整解压（不是只复制了 exe）。"],
                  ["首次启动很久没进界面", "正在导入模型，属正常，耐心等待几分钟，勿关闭。"],
                  ["提示“连接 Ollama 失败 / 404”", "多为 _internal 文件夹丢失或被误删。请重新解压完整文件夹，保持 exe 与 _internal 在一起。"],
                  ["回答很慢", "纯 CPU 推理正常现象；换用更小的模型（如 1b/1.7b）可明显提速。"],
                  ["系统盘空间不足", "清理出约 15 GB 空闲后再首次运行；或用 start_models_on_D.bat 把模型改存到其他盘（见第十部分）。"],
                  ["想卸载", "双击 uninstall_offline_chat.bat 一键彻底清除（程序、模型、环境变量、快捷方式，见第十部分）；也可手动删除 offline_chat 文件夹与 C:\\Users\\你的用户名\\.ollama。"],
              ],
              widths=[4.2, 9.0])

    # ---------- 9. 仍无法安装时的排查 ----------
    add_heading(doc, "九、如果仍然无法正常安装 / 启动", 1)
    add_para(doc, "极少数情况下可能遇到安装失败（如被杀毒软件拦截、解压不完整）。请按顺序排查：")
    add_number(doc, "确认解压完整：打开 offline_chat\\_internal\\bundle_models，应能看到 9 个以 .gguf 结尾的模型文件"
                    "（每个几百 MB 到 2~3 GB）。若某文件大小为 0 或缺失，说明解压不完整，请用 7-Zip 重新解压。")
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
    add_heading(doc, "十、随包附带的三个辅助工具", 1)
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

    # ---------- 结尾 ----------
    doc.add_paragraph()
    end = doc.add_paragraph()
    end.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cjk(end.add_run("本软件完全离线运行，请放心使用。如有疑问，联系发送给你此压缩包的人即可。"),
            CJK, 9.5, False, RGBColor(0x88, 0x88, 0x88))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc.save(OUT)
    print("SAVED:", OUT)


if __name__ == "__main__":
    main()
