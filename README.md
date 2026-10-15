# offline-ai · 离线 AI 智能体

本地离线大语言模型（Ollama）聊天工具 + MCP 桥接智能体。无需联网即可在本地运行开源模型，并通过 MCP 把本地模型能力接入 WorkBuddy 等外部智能体。

## 功能特性

- **内置便携版 Ollama**：首次启动自动导入 9 个开源 GGUF 模型（≤4B，适配纯 CPU 环境，约 14GB）。
- **离线聊天界面**：基于 PySide6 的桌面 UI，支持多模型切换、对话历史。
- **MCP 桥接**：`server/` 下的 MCP server 把本地模型能力桥接给外部智能体，并支持智能体读写工作区（双向集成）。
- **分发配套工具**：模型缺失修复、换盘存储、彻底卸载，见下。

## 目录结构

| 路径 | 说明 |
|------|------|
| `chatui/` | 离线聊天 UI、PyInstaller 打包配置、模型分发与运维工具 |
| `server/` | MCP server（桥接本地模型与外部智能体） |
| `bridge/` | 文件桥接协议 |
| `config.yaml` | 运行时配置 |

## 构建与打包

模型权重与便携 Ollama 体积较大，不随源码仓库分发。打包流程：

1. 准备模型：将 GGUF 与对应 `Modelfile` 放入 `chatui/bundle_models/<model>/`。
2. 准备便携 Ollama：将 `ollama.exe` 及 `lib/` 放入 `chatui/ollama/`。
3. 执行 `pyinstaller chatui/offline_chat.spec` 生成 `chatui/dist/offline_chat`。
4. 运行 `python chatui/verify_dist.py` 做发布前自检（缺 Modelfile / 根目录 DLL 污染 / 缺辅助工具等会中止打包）。

## 获取模型

因权重约 14GB，请自行从 ModelScope / HuggingFace 下载对应 GGUF，或运行：
`python chatui/pull_models.py`（脚本内含 ModelScope 下载地址）。

## 分发运维工具（`chatui/` 目录）

| 工具 | 用途 |
|------|------|
| `diagnose.bat` | 一键诊断：Ollama 服务、模型导入、Modelfile 完整性 |
| `store_fix/start_models_on_D.bat` | C 盘空间不足时，把模型库存放重定向到其他盘 |
| `uninstall_tools/uninstall_offline_chat.bat` | 彻底卸载：应用、模型、缓存、环境变量、快捷方式 |
| `make_modelfile_fix.py` | 为已分发但缺少 Modelfile 的包生成补丁（`modelfile_fix.zip`） |

## 已知问题与修复记录

- **接收方“连接 Ollama 失败 / HTTP 404”**：早期分发包的 PyInstaller `spec` 只打包了 GGUF、漏打包 `Modelfile`，导致 `ollama create` 缺失、9 个模型全部跳过安装。修复：spec 补充 Modelfile 数据；首次启动将“缺失 Modelfile”视为安装失败并重试；并为已分发的用户产出 `modelfile_fix` 补丁。
- **大模型导入失败**：接收方 C 盘剩余空间不足（<15GB）导致 5 个最大模型导入失败。提供 `store_fix` 换盘工具，将 `OLLAMA_MODELS` 指向其他盘。
- **卸载脚本在特殊路径下崩溃**：路径含空格 / 括号 / `&` 时，`cmd` 的 `if (...) else (...)` 块解析失败。重写为基于 `goto` 流程 + 延迟变量展开（v3），并在沙箱中于含空格、中文、括号、`&` 的敌对路径下验证通过。

## 许可证

本项目以 MIT 许可证开源（详见 `LICENSE`）。模型权重遵循各自上游许可证。
