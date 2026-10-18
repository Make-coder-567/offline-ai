# offline-ai · 离线 AI 智能体

本地离线大语言模型（Ollama）聊天工具 + MCP 桥接智能体。无需联网即可在本地运行开源模型，并通过 MCP 把本地模型能力接入 WorkBuddy 等外部智能体。

## 功能特性

- **内置便携版 Ollama**：首次启动自动导入 10 个开源 GGUF 模型（含 1 个视觉模型，约 18GB，纯 CPU 可用）。
- **离线聊天界面**：基于 PySide6 的桌面 UI，支持多模型切换、多轮对话、Markdown、会话持久化、停止生成、参数面板。
- **文档理解（RAG）**：拖入 PDF / Word / Excel / PPT / ePub / 图片即可问答；扫描件与图片走本地 OCR，
  长文档自动入库检索并带 `[1][2]` 引用（`server/docparse.py`）。
- **语义混合检索**：BM25 + 本地向量（bge-small-zh-v1.5，ONNX，512 维）加权 RRF 融合，
  能召回"用词不同但语义相同"的段落；向量模型缺失时自动退回纯 BM25（`server/embed.py`）。
- **深度思考模式**：草稿 → 自检挑刺 → 重写定稿（三轮本地推理，质量换时间），界面一键开关，MCP `deep=true`。
- **离线语音**：麦克风输入用 faster-whisper 本地转写；回答可用 piper 中文语音朗读（模型随包，无网可用）。
- **多会话**：新建 / 切换 / 重命名 / 删除 / 导出 Markdown；旧的 `session.json` 自动迁移。
- **长期记忆**：`memory.md` 记录你的偏好与背景，随每轮对话注入系统提示。
- **定时任务**：每 N 分钟或每天 HH:MM 触发一次本地推理，结果写入 `schedule.log`。
- **MCP 桥接**：`server/` 下的 MCP server 把本地模型能力桥接给外部智能体（22 个工具，见下）。
- **OpenAI 兼容网关**：`python server/gateway.py` 后，任意 OpenAI 客户端把 base_url 指向
  `http://127.0.0.1:8080/v1` 即可直连本机模型。
- **多模态**：聊天窗口支持拖拽 / Ctrl+V 粘贴图片；配合 `gemma3:4b-vision`（Q4_K_M + mmproj-F16）理解图像；
  图片附件还可一键"提取文字"（本地 OCR）。
- **agent 行动能力**：ReAct v2（list_dir / read_file / search_text / write_file / run_python / finish），
  写文件前自动快照、操作记审计日志、可一键回滚；代码执行默认关闭。
- **分发配套工具**：模型缺失修复、换盘存储、彻底卸载，见下。

## 快速开始

### 0. 环境准备

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | **3.10+** | 仅开发态需要；打包版自带运行时 |
| Ollama | 任意近期版本，服务在 `http://127.0.0.1:11434` | 先启动 `ollama serve`（桌面版会自动常驻） |
| 模型 | 至少一个，建议 `gemma3:4b` | 见下方「准备模型」 |

```bash
git clone https://github.com/Make-coder-567/offline-ai.git
cd offline-ai
python -m venv .venv && .venv\Scripts\activate   # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
```

### 1. 桌面聊天（GUI）

```bash
# 开发态（需 Ollama 在跑）
python chatui/chat_app.py
# 打包态：双击 offline_chat.exe（无需 Python）
```

**准备模型**（二选一）：

```bash
ollama pull gemma3:4b          # 最简单；联网一次即可
python chatui/pull_models.py    # 从 ModelScope 批量拉取并导入（含视觉/mmproj，国内更快）
```

- 界面：左侧会话栏（新建 / 搜索 / 会话列表 / 记忆 / 设置 / 服务状态）+ 中间阅读列 + 底部输入卡
- 主题：更多菜单里可切「夜读模式」（暖炭底，QSettings 记忆）；配色令牌在 `chatui/theme.py`
- 左侧「设置」：temperature / 上下文 / 系统提示词（会记住）
- 顶栏模型名 → 自定义浮层（显示体积、当前项打勾）：默认 `gemma3:4b`；图片问答选 `gemma3:4b-vision`
- **拖入图片**或 **Ctrl+V 粘贴图片**即可附图发送；图片上可点「提取文字」走本地 OCR
- **拖入文档**（PDF/Word/Excel/PPT/ePub）：短文档直接随消息发送，长文档自动入库检索后作答
- 点「深度」开启深度思考（草稿→自检→定稿）；点麦克风用语音输入；AI 回答上可点「朗读」
- 会话列表：点一下切换；右键可 置顶 / 重命名 / 导出 Markdown / 删除；搜索框按标题过滤
- 左下「记忆」：记住偏好、查看全部、忘掉某条、开关记忆注入
- 回答里的 `[1][2]` 可点，弹出该段来源；生成中末尾有 `▍` 光标
- `Esc` 停止生成；`Ctrl+L` 清空当前会话；上翻历史不会被强行拉回底部
- 鼠标悬停消息 → 「复制」；**右键复制按钮**可选 Markdown / 纯文本

### 2. 智能体（文件桥）

```bash
bash start_agent.sh          # Windows 也可双击 start_agent.bat
```

在 `.offline-bridge/inbox/` 放任务 JSON：

```json
{"task_id": "t1", "prompt": "先列出 offline-ai 目录，再读 README 概括项目",
 "files": [], "model": null, "task_type": null}
```

- 结果写入 `.offline-bridge/outbox/t1.json`（`status`: done/error/cancelled）
- 取消：在 inbox 放空文件 `t1.cancel`，agent 会在下一步前中止
- `task_type` 可选 `code` / `fast` / `reason` / `vision`，按 `config.yaml` 的 `routes` 选模型

### 3. MCP（在 WorkBuddy 里调用本地模型）

`~/.workbuddy/mcp.json` 已配置 `offline-ai`。修改配置后需**重启 WorkBuddy** 或在连接器页重新信任。

22 个工具：

| 分类 | 工具 |
|------|------|
| 对话 | `local_llm_chat(deep=?)` / `local_llm_summarize` / `local_llm_structured` / `local_llm_code` / `local_model_list` |
| 文档 | `local_doc_text` / `local_doc_ask` / `local_ocr` |
| 检索 | `local_rag_index` / `local_rag_query` / `local_rag_answer` / `local_llm_embed` |
| 视觉 | `local_llm_vision` |
| 行动 | `local_run_python`（默认关闭） / `local_audit` / `local_undo` |
| 记忆与计划 | `local_memory_add` / `local_memory_search` / `local_memory_forget` / `local_schedule_add` / `local_schedule_list` / `local_schedule_remove` |

在 WorkBuddy 里直接说「用 local_model_list 看看本机有哪些模型」即可触发。

### 4. 本地检索与文档问答

1. 建索引：`local_rag_index`（`force=true` 强制重建，`ocr=false` 可跳过图片/扫描件）
2. 检索片段：`local_rag_query(question, top_k=4, hybrid=true)`
3. 基于资料回答并附来源：`local_rag_answer(question, top_k=3)`
4. 单文档问答：`local_doc_ask(path, question)`（解析→入库→检索→作答，带引用）
5. 纯看内容：`local_doc_text(path)` / 扫描件识别：`local_ocr(path)`

索引存放于 `.offline-rag/index.json`（向量在 `vectors.npy`，float16）；
源文件修改后再次调用 index 会按 mtime/size 增量更新，向量也只补算新片段。

### 5. OpenAI 兼容网关

```bash
python server/gateway.py --port 8080        # 默认模型 gemma3:4b
```

支持 `GET /v1/models`、`POST /v1/chat/completions`（含 SSE 流式），
任何 OpenAI 协议客户端把 `base_url` 设为 `http://127.0.0.1:8080/v1` 即可。

### 6. 定时任务

```bash
python server/scheduler.py                  # 常驻，每 20 秒检查一次
```

任务写在 `<data_dir>/schedules.json`（也可通过 MCP `local_schedule_add` 添加），
结果追加到 `<data_dir>/schedule.log`。

## 任务格式（`.offline-bridge/inbox/*.json`）

```json
{
  "task_id": "t1",
  "prompt": "先列出 offline-ai 目录，再读 README 概括项目",
  "files": [],
  "model": null,
  "task_type": null
}
```

- `model`：显式指定模型（优先级最高，可选）
- `task_type`：`code` / `fast` / `reason` / `vision`，按 `config.yaml` 的 `routes` 选择模型
- `files`：附带读取的文件（≤10 个，总计 ≤24000 字符）
- 取消任务：在 `inbox/` 放置空文件 `<task_id>.cancel`，agent 会在下一步前中止（结果状态 `cancelled`）

## 目录结构

| 路径 | 说明 |
|------|------|
| `chatui/` | 离线聊天 UI（`chat_app.py`）、多会话（`sessions.py`）、语音（`voice.py`）、打包配置与运维工具 |
| `chatui/model_assets/` | 本地小模型：bge-small-zh 向量 / piper 语音 / whisper-base 识别（共约 306MB） |
| `server/` | MCP server 与各能力模块：`docparse`（文档解析）/ `rag`（检索）/ `embed`（向量）/ `audit`（审计）/ `tools`（工具集）/ `agent`（ReAct）/ `memory`（长期记忆）/ `scheduler`（定时任务）/ `gateway`（OpenAI 网关）/ `paths`（数据目录） |
| `bridge/` | 文件桥接协议 |
| `config.yaml` | 运行时配置（模型、路由、`agent.allow_python` 等） |

## 构建与打包

模型权重与便携 Ollama 体积较大，不随源码仓库分发。打包流程：

1. 准备模型：`python chatui/pull_models.py` 下载 GGUF 到 `chatui/gguf_models/`，
   再 `python chatui/prepare_bundle.py` 生成 `bundle_manifest.json` 与 `bundle_models/*/Modelfile`
   （视觉模型会自动带上 `mmproj-F16.gguf`，manifest 里写 `mmproj_name/mmproj_abs`）。
2. 准备便携 Ollama：将 `ollama.exe` 及 `lib/` 放入 `chatui/ollama/`。
3. 在 `chatui/` 下执行：
   ```bash
   python -m PyInstaller --noconfirm --clean --distpath dist --workpath build offline_chat.spec
   ```
   产物为 `dist/offline_chat/`（exe + `_internal`，约 17.3GB，含 10 个模型、视觉投影、
   `server/` 与 `model_assets/`）。**若旧包正在运行会占住目录导致构建失败，先关闭程序**。
4. 发布前自检：
   ```bash
   python verify_dist.py dist/offline_chat
   ```
5. 分发：用 7-Zip 把整个产物文件夹打包为 zip（约 17GB）。**不能只发 exe**，必须连同 `_internal/` 一起。

### 轻量单文件 exe（可选）

不含模型与 Ollama，依赖本机已安装的 Ollama（体积约 200MB，onnxruntime 占大头）：

```bash
cd chatui
set OFFLINE_LITE=1 && python -m PyInstaller --noconfirm --clean ^
  --distpath dist_lite --workpath build_lite offline_chat.spec
```

产物 `dist_lite/offline_chat_lite.exe`（单文件）。首次使用会自动 `ollama pull` 默认模型（需联网）。
注意：轻装版不带 `model_assets/`，文档向量检索与语音按钮会自动降级/隐藏。

## 获取模型（重建打包时才需要）

源码仓库**不保留** GGUF 权重（约 19GB，`.gitignore` 已忽略，本机已按需删除）。
要重新打包分发包时，先把权重下载回 `chatui/gguf_models/`：

```bash
python chatui/pull_models.py     # 走 ModelScope，落盘到 chatui/gguf_models/
python chatui/prepare_bundle.py  # 生成 bundle_manifest.json + bundle_models/*/Modelfile
```

`chatui/bundle_models/*/Modelfile`（纯文本）随仓库保留，因此只要有 GGUF 就能直接重建。
已安装到本机 Ollama 的模型不受影响（存在 `%USERPROFILE%\.ollama`），删除权重不影响日常使用。

### 视觉模型（可选）

`gemma3:4b-vision` 由文本 GGUF + `mmproj-F16.gguf`（约 851MB）组合而成：

```
FROM ./gemma-3-4b-it-Q4_K_M.gguf
FROM ./gemma-3-4b-mmproj-F16.gguf
# 其余 TEMPLATE / PARAMETER 从 `ollama show gemma3:4b --modelfile` 复制
```

生成后 `ollama create gemma3:4b-vision -f chatui/gguf_models/Modelfile.gemma3_vision`。
聊天窗口拖入图片即可以该模型进行视觉问答；MCP 侧对应 `local_llm_vision`。

## 回归测试

```bash
python tests/smoke_test.py          # 全部（23 组，UI 部分使用 offscreen，无需 Ollama）
python tests/smoke_test.py --no-ui  # 只跑后端
python tests/real_chain_test.py     # 真实链路（需 Ollama 在跑）：文档问答 / 写文件+回滚 / 深度思考
python tests/real_chain_test.py --quick   # 跳过深度思考
```

## 分发运维工具（`chatui/` 目录）

| 工具 | 用途 |
|------|------|
| `diagnose.bat` | 一键诊断：Ollama 服务、模型导入、Modelfile 完整性 |
| `store_fix/start_models_on_D.bat` | C 盘空间不足时，把模型库存放重定向到其他盘 |
| `uninstall_tools/uninstall_offline_chat.bat` | 彻底卸载：应用、模型、缓存、环境变量、快捷方式 |

## 已知问题与修复记录

- **接收方“连接 Ollama 失败 / HTTP 404”**：早期分发包的 PyInstaller `spec` 只打包了 GGUF、漏打包 `Modelfile`，导致 `ollama create` 缺失、9 个模型全部跳过安装。修复：spec 补充 Modelfile 数据；首次启动将“缺失 Modelfile”视为安装失败并重试；并为已分发的用户产出 `modelfile_fix` 补丁。
- **大模型导入失败**：接收方 C 盘剩余空间不足（<15GB）导致 5 个最大模型导入失败。提供 `store_fix` 换盘工具，将 `OLLAMA_MODELS` 指向其他盘。
- **卸载脚本在特殊路径下崩溃**：路径含空格 / 括号 / `&` 时，`cmd` 的 `if (...) else (...)` 块解析失败。重写为基于 `goto` 流程 + 延迟变量展开（v3），并在沙箱中于含空格、中文、括号、`&` 的敌对路径下验证通过。
- **切换模型后回复变成 `0000…` / 乱码**（2026-10-18）：`keep_alive=10m` 让 Ollama 把每个用过的模型都留在内存里，16GB 机器上切两三次就吃满（实测 97%），KV cache 分配失败后推理退化成重复字符流。修复：① 切换模型时后台调用 `/api/generate {keep_alive:0}` 卸载其余模型；② `degenerate_output()` 检测退化输出（长文本单字符≥60% 或全文≤3 种字符），丢弃结果并给出可操作提示，不再写入会话历史。附：同一台机器上 `qwen3:8b` 单模型加载时输出完全正常，删除该模型并非必要。

## 许可证

本项目以 MIT 许可证开源（详见 `LICENSE`）。模型权重遵循各自上游许可证。
